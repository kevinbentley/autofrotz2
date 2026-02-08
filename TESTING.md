# Autofrotz2 Test Plan

## Testing Strategy

### General Guidelines
- Testing should be automated to the greatest extent possible. Tests will be performed in a heirarchial manner, so if unit tests pass, integration tests are performed, and when integration tests pass, a limited run with the LLM will be tested.

The goal is to enable Claude to work in a fully automated manner, to reduce any human in the loop testing until a high level of success is attained.

### Test Details

- Unit tests for map manager (add rooms, add connections, pathfinding, unexplored exits, blocked paths, unidirectional edges, maze detection with identical descriptions, maze room ID generation, marker assignment and lookup).
- Unit tests for item manager (add items, move items, query by location, query by property, get_droppable_items sorting).
- Integration tests with a mock LLM that returns canned responses, verifying the orchestrator correctly sequences agent calls and state updates.
- A "smoke test" mode that plays the game through several moves with a real LLM. Once it's processed 10-20 moves, the log file is read and analyzed for bugs.
