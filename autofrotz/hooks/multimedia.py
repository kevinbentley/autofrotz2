"""
Multimedia hook placeholder for AutoFrotz v2.

Future implementation will generate images for room descriptions
and produce text-to-speech narration of game output.
"""

from autofrotz.hooks.base import BaseHook


class MultimediaHook(BaseHook):
    """
    Placeholder for future multimedia hooks.

    Planned features:
    - Image generation on room entry (DALL-E, Stable Diffusion, etc.)
    - Text-to-speech narration of game output (ElevenLabs, OpenAI TTS, etc.)
    - Image caching by room_id to avoid redundant generation on revisits
    """

    # TODO: Implement on_room_enter to generate room images
    # TODO: Implement on_turn_end to narrate game output via TTS
    # TODO: Add image cache keyed by room_id
    # TODO: Add configuration for image/TTS providers and models
    pass
