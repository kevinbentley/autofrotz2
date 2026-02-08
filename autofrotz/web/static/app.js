// AutoFrotz v2 - Frontend Application

// Global state
const state = {
    currentGameId: null,
    currentMode: 'disconnected', // 'disconnected', 'live', 'replay'
    websocket: null,
    replayData: {
        turns: [],
        currentIndex: 0,
        playing: false,
        speed: 1.0,
        intervalId: null
    },
    currentRoom: null
};

// API Client Functions
async function fetchGames() {
    const response = await fetch('/api/games');
    return await response.json();
}

async function fetchGame(id) {
    const response = await fetch(`/api/games/${id}`);
    return await response.json();
}

async function fetchTurns(id, limit = null, offset = 0) {
    let url = `/api/games/${id}/turns?offset=${offset}`;
    if (limit) url += `&limit=${limit}`;
    const response = await fetch(url);
    return await response.json();
}

async function fetchTurn(id, turnNumber) {
    const response = await fetch(`/api/games/${id}/turns/${turnNumber}`);
    return await response.json();
}

async function fetchMap(id) {
    const response = await fetch(`/api/games/${id}/map`);
    return await response.json();
}

async function fetchItems(id) {
    const response = await fetch(`/api/games/${id}/items`);
    return await response.json();
}

async function fetchPuzzles(id) {
    const response = await fetch(`/api/games/${id}/puzzles`);
    return await response.json();
}

async function fetchMetrics(id) {
    const response = await fetch(`/api/games/${id}/metrics`);
    return await response.json();
}

// UI Update Functions
function updateModeIndicator(mode) {
    const indicator = document.getElementById('mode-indicator');
    state.currentMode = mode;

    if (mode === 'live') {
        indicator.textContent = 'Live';
        indicator.className = 'mode-badge mode-live';
    } else if (mode === 'replay') {
        indicator.textContent = 'Replay';
        indicator.className = 'mode-badge mode-replay';
    } else {
        indicator.textContent = 'Disconnected';
        indicator.className = 'mode-badge mode-disconnected';
    }
}

function appendToTranscript(turnNumber, command, output, reasoning = null) {
    const transcript = document.getElementById('transcript-content');

    // Remove placeholder if present
    const placeholder = transcript.querySelector('.placeholder');
    if (placeholder) placeholder.remove();

    const turnDiv = document.createElement('div');
    turnDiv.className = 'turn-entry';
    turnDiv.dataset.turn = turnNumber;

    const turnHeader = document.createElement('div');
    turnHeader.className = 'turn-header';
    turnHeader.textContent = `Turn ${turnNumber}`;

    const commandDiv = document.createElement('div');
    commandDiv.className = 'command';
    commandDiv.textContent = `> ${command}`;

    const outputDiv = document.createElement('div');
    outputDiv.className = 'output';
    outputDiv.textContent = output;

    turnDiv.appendChild(turnHeader);
    turnDiv.appendChild(commandDiv);
    turnDiv.appendChild(outputDiv);

    if (reasoning) {
        const reasoningDiv = document.createElement('details');
        reasoningDiv.className = 'reasoning';
        const summary = document.createElement('summary');
        summary.textContent = 'Agent Reasoning';
        const content = document.createElement('p');
        content.textContent = reasoning;
        reasoningDiv.appendChild(summary);
        reasoningDiv.appendChild(content);
        turnDiv.appendChild(reasoningDiv);
    }

    transcript.appendChild(turnDiv);

    // Auto-scroll to bottom in live mode
    if (state.currentMode === 'live') {
        transcript.scrollTop = transcript.scrollHeight;
    }
}

function clearTranscript() {
    const transcript = document.getElementById('transcript-content');
    transcript.innerHTML = '<p class="placeholder">Select a game to view transcript...</p>';
}

function updateInventory(items) {
    const inventoryContent = document.getElementById('inventory-content');

    const inventoryItems = items.filter(item => item.location === 'inventory');

    if (inventoryItems.length === 0) {
        inventoryContent.innerHTML = '<p class="placeholder">No items in inventory</p>';
        return;
    }

    inventoryContent.innerHTML = '';
    const ul = document.createElement('ul');

    inventoryItems.forEach(item => {
        const li = document.createElement('li');
        li.className = 'item';
        li.innerHTML = `<strong>${item.name}</strong>`;
        if (item.description) {
            li.innerHTML += `<br><span class="item-desc">${item.description}</span>`;
        }
        ul.appendChild(li);
    });

    inventoryContent.appendChild(ul);
}

function updatePuzzles(puzzles) {
    const puzzleContent = document.getElementById('puzzle-content');

    if (puzzles.length === 0) {
        puzzleContent.innerHTML = '<p class="placeholder">No puzzles detected</p>';
        return;
    }

    puzzleContent.innerHTML = '';
    const ul = document.createElement('ul');

    puzzles.forEach(puzzle => {
        const li = document.createElement('li');
        li.className = 'puzzle';

        const statusBadge = document.createElement('span');
        statusBadge.className = `status-badge status-${puzzle.status}`;
        statusBadge.textContent = puzzle.status;

        const desc = document.createElement('span');
        desc.textContent = puzzle.description;

        li.appendChild(statusBadge);
        li.appendChild(desc);
        ul.appendChild(li);
    });

    puzzleContent.appendChild(ul);
}

function updateMetrics(metrics) {
    document.getElementById('metric-input-tokens').textContent = metrics.total.input_tokens.toLocaleString();
    document.getElementById('metric-output-tokens').textContent = metrics.total.output_tokens.toLocaleString();
    document.getElementById('metric-cached-tokens').textContent = metrics.total.cached_tokens.toLocaleString();
    document.getElementById('metric-cost').textContent = `$${metrics.total.cost_estimate.toFixed(4)}`;
    document.getElementById('metric-latency').textContent = `${metrics.total.total_latency_ms.toLocaleString()}ms`;

    // Agent breakdown
    const agentMetrics = document.getElementById('agent-metrics');
    agentMetrics.innerHTML = '';

    for (const [agentName, data] of Object.entries(metrics.by_agent)) {
        const agentDiv = document.createElement('div');
        agentDiv.className = 'agent-metric';
        agentDiv.innerHTML = `
            <h4>${agentName}</h4>
            <p>Provider: ${data.provider} / ${data.model}</p>
            <p>Calls: ${data.call_count}</p>
            <p>Input: ${data.input_tokens.toLocaleString()} | Output: ${data.output_tokens.toLocaleString()} | Cached: ${data.cached_tokens.toLocaleString()}</p>
            <p>Cost: $${data.cost_estimate.toFixed(4)} | Latency: ${data.total_latency_ms.toLocaleString()}ms</p>
        `;
        agentMetrics.appendChild(agentDiv);
    }
}

function renderMap(mapData) {
    const svg = document.getElementById('map-svg');
    const edgesGroup = document.getElementById('map-edges');
    const nodesGroup = document.getElementById('map-nodes');

    // Clear existing content
    edgesGroup.innerHTML = '';
    nodesGroup.innerHTML = '';

    if (!mapData.nodes || mapData.nodes.length === 0) {
        return;
    }

    // Simple grid layout for nodes
    const nodeWidth = 120;
    const nodeHeight = 60;
    const padding = 40;
    const cols = Math.ceil(Math.sqrt(mapData.nodes.length));

    // Assign positions to nodes
    const nodePositions = {};
    mapData.nodes.forEach((node, index) => {
        const col = index % cols;
        const row = Math.floor(index / cols);
        nodePositions[node.id] = {
            x: col * (nodeWidth + padding) + padding,
            y: row * (nodeHeight + padding) + padding,
            node: node
        };
    });

    // Calculate SVG dimensions
    const maxX = Math.max(...Object.values(nodePositions).map(p => p.x)) + nodeWidth + padding;
    const maxY = Math.max(...Object.values(nodePositions).map(p => p.y)) + nodeHeight + padding;
    svg.setAttribute('viewBox', `0 0 ${maxX} ${maxY}`);

    // Draw edges
    mapData.edges.forEach(edge => {
        const from = nodePositions[edge.from];
        const to = nodePositions[edge.to];

        if (!from || !to) return;

        const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        line.setAttribute('x1', from.x + nodeWidth / 2);
        line.setAttribute('y1', from.y + nodeHeight / 2);
        line.setAttribute('x2', to.x + nodeWidth / 2);
        line.setAttribute('y2', to.y + nodeHeight / 2);
        line.setAttribute('class', edge.blocked ? 'edge-blocked' : 'edge');

        if (edge.teleport) {
            line.setAttribute('stroke-dasharray', '5,5');
        }

        edgesGroup.appendChild(line);

        // Direction label
        const midX = (from.x + to.x) / 2 + nodeWidth / 4;
        const midY = (from.y + to.y) / 2 + nodeHeight / 4;
        const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        text.setAttribute('x', midX);
        text.setAttribute('y', midY);
        text.setAttribute('class', 'edge-label');
        text.textContent = edge.direction;
        edgesGroup.appendChild(text);
    });

    // Draw nodes
    Object.entries(nodePositions).forEach(([nodeId, pos]) => {
        const node = pos.node;

        const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        rect.setAttribute('x', pos.x);
        rect.setAttribute('y', pos.y);
        rect.setAttribute('width', nodeWidth);
        rect.setAttribute('height', nodeHeight);
        rect.setAttribute('rx', 5);

        let className = 'node';
        if (node.id === state.currentRoom) {
            className += ' node-current';
        } else if (node.visited) {
            className += ' node-visited';
        } else {
            className += ' node-unvisited';
        }

        if (node.is_dark) {
            className += ' node-dark';
        }

        rect.setAttribute('class', className);

        const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        text.setAttribute('x', pos.x + nodeWidth / 2);
        text.setAttribute('y', pos.y + nodeHeight / 2);
        text.setAttribute('class', 'node-label');
        text.setAttribute('text-anchor', 'middle');
        text.setAttribute('dominant-baseline', 'middle');
        text.textContent = node.name || node.id;

        const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
        title.textContent = `${node.name}\nVisits: ${node.visit_count}\n${node.description || ''}`;

        nodesGroup.appendChild(rect);
        nodesGroup.appendChild(text);
        rect.appendChild(title);
    });
}

// WebSocket Live Mode
function connectLive(gameId) {
    if (state.websocket) {
        state.websocket.close();
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/live/${gameId}`;

    state.websocket = new WebSocket(wsUrl);

    state.websocket.onopen = () => {
        console.log('WebSocket connected');
        updateModeIndicator('live');
    };

    state.websocket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleLiveEvent(data);
    };

    state.websocket.onerror = (error) => {
        console.error('WebSocket error:', error);
        updateModeIndicator('disconnected');
    };

    state.websocket.onclose = () => {
        console.log('WebSocket disconnected');
        updateModeIndicator('disconnected');

        // Auto-reconnect after 5 seconds
        setTimeout(() => {
            if (state.currentGameId === gameId && state.currentMode === 'disconnected') {
                console.log('Attempting to reconnect...');
                connectLive(gameId);
            }
        }, 5000);
    };
}

async function refreshAllPanels() {
    if (!state.currentGameId) return;
    try {
        const [items, puzzles, mapData, metrics] = await Promise.all([
            fetchItems(state.currentGameId),
            fetchPuzzles(state.currentGameId),
            fetchMap(state.currentGameId),
            fetchMetrics(state.currentGameId)
        ]);
        updateInventory(items);
        updatePuzzles(puzzles);
        renderMap(mapData);
        updateMetrics(metrics);
    } catch (e) {
        console.error('Error refreshing panels:', e);
    }
}

function handleLiveEvent(event) {
    switch (event.type) {
        case 'connected':
            console.log('Live feed connected:', event.message);
            break;
        case 'turn':
            appendToTranscript(event.turn_number, event.command, event.output, event.agent_reasoning);
            if (event.room && event.room.id) {
                state.currentRoom = event.room.id;
            }
            // Refresh all panels from DB after each turn
            refreshAllPanels();
            break;
        case 'room_enter':
            state.currentRoom = event.room_id;
            break;
        case 'item_found':
        case 'item_taken':
        case 'puzzle_found':
        case 'puzzle_solved':
        case 'maze_detected':
        case 'maze_completed':
            // Refresh panels on any state-changing event
            refreshAllPanels();
            break;
        case 'game_end':
            refreshAllPanels();
            updateModeIndicator('replay');
            break;
        default:
            console.log('Live event:', event.type, event);
    }
}

// Replay Mode
async function startReplay(gameId) {
    updateModeIndicator('replay');
    document.getElementById('replay-controls').classList.remove('hidden');

    // Load all turns
    const turns = await fetchTurns(gameId);
    state.replayData.turns = turns;
    state.replayData.currentIndex = 0;

    document.getElementById('total-turns').textContent = turns.length;

    if (turns.length > 0) {
        await showReplayTurn(0);
    }
}

async function showReplayTurn(index) {
    if (index < 0 || index >= state.replayData.turns.length) return;

    state.replayData.currentIndex = index;
    const turn = state.replayData.turns[index];

    // Update transcript (show only up to current turn)
    clearTranscript();
    for (let i = 0; i <= index; i++) {
        const t = state.replayData.turns[i];
        appendToTranscript(t.turn_number, t.command_sent, t.game_output, t.agent_reasoning);
    }

    // Update current room
    state.currentRoom = turn.room_id;

    // Update turn indicator
    document.getElementById('current-turn').textContent = turn.turn_number;

    // Update inventory from snapshot
    if (turn.inventory_snapshot) {
        const inventoryItems = turn.inventory_snapshot.map(itemId => ({
            name: itemId,
            location: 'inventory'
        }));
        updateInventory(inventoryItems);
    }

    // Refresh map, puzzles, and metrics
    const [mapData, puzzles, metrics] = await Promise.all([
        fetchMap(state.currentGameId),
        fetchPuzzles(state.currentGameId),
        fetchMetrics(state.currentGameId)
    ]);

    renderMap(mapData);
    updatePuzzles(puzzles);
    updateMetrics(metrics);
}

function togglePlayPause() {
    const btn = document.getElementById('btn-play-pause');

    if (state.replayData.playing) {
        // Pause
        state.replayData.playing = false;
        clearInterval(state.replayData.intervalId);
        btn.textContent = '▶';
    } else {
        // Play
        state.replayData.playing = true;
        btn.textContent = '⏸';

        const speed = state.replayData.speed;
        const intervalMs = 1000 / speed;

        state.replayData.intervalId = setInterval(() => {
            if (state.replayData.currentIndex >= state.replayData.turns.length - 1) {
                togglePlayPause(); // Auto-pause at end
                return;
            }
            showReplayTurn(state.replayData.currentIndex + 1);
        }, intervalMs);
    }
}

// Event Handlers
async function onGameSelected() {
    const selector = document.getElementById('game-selector');
    const gameId = selector.value;

    if (!gameId) {
        clearTranscript();
        updateModeIndicator('disconnected');
        document.getElementById('replay-controls').classList.add('hidden');
        return;
    }

    state.currentGameId = gameId;

    // Check game status to determine mode
    const game = await fetchGame(gameId);
    document.getElementById('metric-turns').textContent = game.total_turns || 0;

    if (game.status === 'playing') {
        // Live mode
        connectLive(gameId);
        document.getElementById('replay-controls').classList.add('hidden');

        // Load initial state
        const [turns, items, puzzles, mapData, metrics] = await Promise.all([
            fetchTurns(gameId),
            fetchItems(gameId),
            fetchPuzzles(gameId),
            fetchMap(gameId),
            fetchMetrics(gameId)
        ]);

        clearTranscript();
        turns.forEach(t => {
            appendToTranscript(t.turn_number, t.command_sent, t.game_output, t.agent_reasoning);
        });

        if (turns.length > 0) {
            state.currentRoom = turns[turns.length - 1].room_id;
        }

        updateInventory(items);
        updatePuzzles(puzzles);
        renderMap(mapData);
        updateMetrics(metrics);
    } else {
        // Replay mode
        if (state.websocket) {
            state.websocket.close();
            state.websocket = null;
        }
        await startReplay(gameId);
    }
}

// Initialize
async function init() {
    // Load game list
    const games = await fetchGames();
    const selector = document.getElementById('game-selector');

    games.forEach(game => {
        const option = document.createElement('option');
        option.value = game.game_id;
        option.textContent = `${game.game_file} - ${game.status} (${game.total_turns || 0} turns)`;
        selector.appendChild(option);
    });

    // Auto-select the latest active game, or the most recent game
    const activeGame = games.find(g => g.status === 'playing');
    const latestGame = activeGame || (games.length > 0 ? games[games.length - 1] : null);
    if (latestGame) {
        selector.value = latestGame.game_id;
        // Trigger selection
        onGameSelected();
    }

    // Periodically refresh the game list to pick up new games
    setInterval(async () => {
        if (state.currentMode !== 'live') return;
        try {
            const freshGames = await fetchGames();
            // Update dropdown options
            const existingIds = new Set(Array.from(selector.options).map(o => o.value));
            freshGames.forEach(g => {
                if (!existingIds.has(String(g.game_id))) {
                    const option = document.createElement('option');
                    option.value = g.game_id;
                    option.textContent = `${g.game_file} - ${g.status} (${g.total_turns || 0} turns)`;
                    selector.appendChild(option);
                }
            });
        } catch (e) {
            // Ignore refresh errors
        }
    }, 10000);

    // Event listeners
    selector.addEventListener('change', onGameSelected);

    // Replay controls
    document.getElementById('btn-first').addEventListener('click', () => {
        showReplayTurn(0);
    });

    document.getElementById('btn-prev').addEventListener('click', () => {
        showReplayTurn(state.replayData.currentIndex - 1);
    });

    document.getElementById('btn-play-pause').addEventListener('click', togglePlayPause);

    document.getElementById('btn-next').addEventListener('click', () => {
        showReplayTurn(state.replayData.currentIndex + 1);
    });

    document.getElementById('btn-last').addEventListener('click', () => {
        showReplayTurn(state.replayData.turns.length - 1);
    });

    document.getElementById('speed-slider').addEventListener('input', (e) => {
        const speed = parseFloat(e.target.value);
        state.replayData.speed = speed;
        document.getElementById('speed-value').textContent = `${speed}x`;

        // If playing, restart interval with new speed
        if (state.replayData.playing) {
            clearInterval(state.replayData.intervalId);
            const intervalMs = 1000 / speed;
            state.replayData.intervalId = setInterval(() => {
                if (state.replayData.currentIndex >= state.replayData.turns.length - 1) {
                    togglePlayPause();
                    return;
                }
                showReplayTurn(state.replayData.currentIndex + 1);
            }, intervalMs);
        }
    });
}

// Start app when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
