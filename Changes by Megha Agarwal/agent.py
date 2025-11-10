from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RunContext,
    WorkerOptions,
    cli,
    function_tool,
    llm,
)
from livekit.plugins import deepgram, openai, silero, google, speechify
from interrupt_extension import InterruptExtension
from dotenv import load_dotenv
import logging
import asyncio

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@function_tool
async def lookup_weather(
    context: RunContext,
    location: str,
):
    """Used to look up weather information."""
    return {"weather": "sunny", "temperature": 70}


async def entrypoint(ctx: JobContext):
    await ctx.connect()
    
    logger.info("Starting voice agent with custom interrupt handling...")

    # Create the agent
    agent = Agent(
        instructions="You are a friendly voice assistant built by LiveKit. You can search internet and tell anything. Keep your responses concise and natural.",
    )

    # Create session components
    # Choose your TTS - pick ONE of these options:
    
    # Option 1: OpenAI TTS (Recommended - best quality, pay-as-you-go)
    # Requires OPENAI_API_KEY in .env
    session = AgentSession(
        vad=silero.VAD.load(),
        stt=deepgram.STT(model="nova-3"),
        llm=google.LLM(model="gemini-2.0-flash"),
        tts=speechify.TTS(
            model="simba-english",
            voice_id="kristy",
        ),
        # Interrupt handling settings
        allow_interruptions=True,  # Enable interruptions
        min_interruption_words=3,  # Require at least 2 words to interrupt (filters "okay", "hmm")
        min_interruption_duration=1.2,  # Require 0.8 seconds of speech (filters quick fillers)
    )
    
    logger.info(f" Session components initialized")
    
    # Create and initialize the interrupt extension
    # Pass session instead of agent
    interrupt_ext = InterruptExtension(
        session=session,
        tts=session.tts,
        stt=session.stt,
        vad=session.vad,
        llm=session.llm,
        # Configure filler words that are ONLY ignored during agent speech
        ignored_words=["uh", "um", "umm", "hmm", "mm", "mhm", "ah", "eh"],
        min_conf=0.6,  # Minimum confidence threshold for processing
    )
    await interrupt_ext.init()

    # Track if we should allow the next potential interrupt
    allow_next_interrupt = True
    
    # Wire up interrupt extension to session events
    # Handle agent state changes (initializing, listening, thinking, speaking)
    @session.on("agent_state_changed")
    def on_agent_state(event):
        state = event.state if hasattr(event, 'state') else str(event)
        asyncio.create_task(interrupt_ext.on_agent_state_changed(state))
    
    # Intercept transcripts to do custom filtering
    @session.on("user_input_transcribed")
    def on_user_transcript(event):
        async def process():
            text = event.transcript if hasattr(event, 'transcript') else str(event)
            conf = event.confidence if hasattr(event, 'confidence') else 1.0
            is_final = event.is_final if hasattr(event, 'is_final') else True
            
            if not is_final:
                return
            
            async with interrupt_ext._lock:
                speaking = interrupt_ext.agent_speaking
            
            logger.info(f" Transcript: '{text}' (conf={conf:.2f}, agent_speaking={speaking})")
            
            # If agent is speaking, evaluate for interrupt
            if speaking:
                should_interrupt = await interrupt_ext._evaluate_for_interrupt(text, conf)
                
                if not should_interrupt:
                    # This should be ignored - manually interrupt to stop any processing
                    logger.info(f" Preventing interrupt for: {text!r}")
                    # Don't call session.interrupt() - just log and return
                    return
                else:
                    # This is a valid interrupt - let it proceed naturally
                    logger.info(f" Allowing interrupt for: {text!r}")
            else:
                # Agent not speaking - all input is valid
                logger.info(f"User input while agent quiet: {text!r}")
        
        asyncio.create_task(process())
    
    logger.info(" Event handlers connected to interrupt extension")

    # Start the agent session
    await session.start(agent=agent, room=ctx.room)
    logger.info(" Agent session started in room")
    
    # Generate initial greeting
    try:
        reply = await session.generate_reply(
            instructions="Greet the user warmly and ask how you can help them today."
        )
        logger.info(f" Initial greeting generated")
    except Exception as e:
        logger.error(f" Error generating initial greeting: {e}")


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))