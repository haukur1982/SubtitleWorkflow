import os
import logging
from openai import OpenAI
from pathlib import Path

# Configure logging
logger = logging.getLogger(__name__)

class OpenAITTSProvider:
    """
    Provider for OpenAI's Text-to-Speech API.
    Voices: alloy, echo, fable, onyx, nova, shimmer
    """
    def __init__(self, api_key=None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            logger.warning("OpenAI API Key not found. TTS will fail.")
        self.client = OpenAI(api_key=self.api_key)

    def generate_speech(self, text: str, output_path: Path, voice: str = "alloy", speed: float = 1.0) -> Path:
        """
        Generates speech from text and saves it to the specified output path.
        
        Args:
            text: The text to convert to speech.
            output_path: Path to save the audio file (should end in .mp3 or .aac).
            voice: The OpenAI voice ID to use (default: "alloy").
            speed: Playback speed (0.25 to 4.0).
            
        Returns:
            Path object of the saved file.
        """
        try:
            # Ensure output directory exists
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            logger.info(f"Generating TTS (OpenAI): voice={voice}, len={len(text)} chars")
            
            response = self.client.audio.speech.create(
                model="tts-1",
                voice=voice,
                input=text,
                speed=speed
            )
            
            # Stream to file
            response.stream_to_file(output_path)
            
            if not output_path.exists() or output_path.stat().st_size == 0:
                raise Exception("TTS Output file is empty or missing")
                
            return output_path
            
        except Exception as e:
            logger.error(f"OpenAI TTS Failed: {e}")
            raise e
