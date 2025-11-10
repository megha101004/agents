import asyncio
import logging

logger = logging.getLogger("interrupt_extension")
logger.setLevel(logging.INFO)

class InterruptExtension:
    def __init__(self, session, tts, stt, vad, llm, ignored_words=None, min_conf=0.6):
        self.session = session
        self.tts = tts
        self.stt = stt
        self.vad = vad
        self.llm = llm
        self.ignored_words = set(ignored_words or ["uh", "umm", "hmm", "mm"])
        self.min_conf = min_conf
        self.agent_speaking = False
        self._lock = asyncio.Lock()

    async def init(self):
        """Initialize interrupt extension."""
        logger.info("Interrupt extension initialized")
        
    async def on_agent_state_changed(self, state: str):
        """Called when agent state changes (initializing, listening, thinking, speaking)."""
        async with self._lock:
            # Agent is speaking when in 'speaking' state
            was_speaking = self.agent_speaking
            self.agent_speaking = (state == "speaking")
            
            if was_speaking != self.agent_speaking:
                logger.info(f"Agent state changed: {state} (agent_speaking={self.agent_speaking})")

    async def _query_intent_with_gemini(self, text: str) -> str:
        """Use Gemini to classify if utterance is an interrupt or should be ignored."""
        prompt = (
            "You are an assistant that classifies user utterances as either 'interrupt' or 'ignore'. "
            "An 'interrupt' means the user wants to stop the current response and redirect the conversation. "
            "An 'ignore' means it's just a filler word, acknowledgment, or continuation that doesn't require stopping. "
            "Given the user's utterance below, respond with exactly one word: 'interrupt' or 'ignore'.\n\n"
            f"User utterance: \"{text}\"\n"
            "Classification:"
        )
        try:
            response = await self.llm.generate(prompt)
            # Assume response.text contains the output
            intent = response.text.strip().lower()
            if intent in {"interrupt", "ignore"}:
                logger.info(f"Gemini classified '{text}' as: {intent}")
                return intent
            else:
                logger.warning(f"Unexpected intent response from Gemini: {intent!r}, defaulting to 'ignore'")
                return "ignore"
        except Exception as e:
            logger.error(f"Error querying Gemini for intent classification: {e}")
            return "ignore"

    async def _evaluate_for_interrupt(self, text: str, conf: float):
        """Evaluate if this transcript during agent speech should trigger an interrupt.
        
        Returns:
            bool: True if this is an interrupt, False if should be ignored
        """
        
        # STEP 1: Check confidence threshold
        if conf < self.min_conf:
            logger.info(f" Ignored low confidence transcript: {text!r} (conf={conf:.2f})")
            return False
        
        # STEP 2: Clean up text and tokenize
        clean = text.strip().lower()
        toks = [t for t in clean.split() if t]
        
        if not toks:
            logger.info(f" Ignored empty transcript")
            return False
        
        # STEP 3: Define filler words (ONLY ignored when agent is speaking)
        filler_words = self.ignored_words.union({
            "uh", "um", "umm", "hmm", "mmmmm", "mhm", "eh", "ah", "er", "huh", "mm"
        })
        
        # STEP 4: Define short acknowledgments (also ignored during agent speech)
        short_ack = {"ok", "okay", "alright", "yeah", "yes", "yep", "sure", "right", "mhm"}
        
        # STEP 5: Rule-based filtering for pure filler words
        # If ALL words are fillers, ignore completely
        if all(t in filler_words for t in toks):
            logger.info(f" Ignored pure filler during agent speech: {text!r}")
            return False
        
        # STEP 6: Single word acknowledgments
        # If it's a single short acknowledgment, ignore
        if len(toks) == 1 and toks[0] in short_ack:
            logger.info(f" Ignored short acknowledgment during agent speech: {text!r}")
            return False
        
        # STEP 7: Mixed filler detection
        # Remove filler words and see what's left
        non_filler_toks = [t for t in toks if t not in filler_words]
        
        # If after removing fillers we only have short acks, ignore
        if all(t in short_ack for t in non_filler_toks):
            logger.info(f" Ignored filler + acknowledgment combo: {text!r}")
            return False
        
        # If we have "umm okay" style (filler + single ack), ignore
        if len(non_filler_toks) == 1 and non_filler_toks[0] in short_ack:
            logger.info(f" Ignored filler with single acknowledgment: {text!r}")
            return False
        
        # STEP 8: Check for strong interrupt keywords (fast path)
        interrupt_keywords = {
            "stop", "wait", "hold", "pause", "no", "actually", "sorry", 
            "excuse", "listen", "hang", "one second", "hold on", "wait a minute"
        }
        
        has_interrupt_keyword = any(
            keyword in clean for keyword in interrupt_keywords
        )
        
        if has_interrupt_keyword:
            logger.warning(f" INTERRUPT DETECTED (keyword match): {text!r}")
            await self._trigger_interrupt()
            return True
        
        # STEP 9: For ambiguous cases, use Gemini as fallback
        # Only if we have meaningful content (not just fillers + ack)
        if len(non_filler_toks) > 0:
            logger.info(f" Evaluating ambiguous input with Gemini: {text!r}")
            intent = await self._query_intent_with_gemini(text)
            
            if intent == "ignore":
                logger.info(f" Gemini says ignore: {text!r}")
                return False
            elif intent == "interrupt":
                logger.warning(f" INTERRUPT DETECTED (Gemini): {text!r}")
                await self._trigger_interrupt()
                return True
        else:
            logger.info(f" No meaningful content after filtering fillers: {text!r}")
            return False
        
        return False
    
    async def _trigger_interrupt(self):
        """Stop TTS and reset agent speaking state."""
        try:
            await self.tts.stop()
            logger.info(" TTS stopped")
        except Exception as e:
            logger.error(f"Error stopping TTS: {e}")
        
        async with self._lock:
            self.agent_speaking = False
        
        logger.info(" Interruption handled, session will process user input")

    async def on_user_input_transcribed(self, event):
        """Handle incoming transcript from STT (UserInputTranscribedEvent)."""
        # Extract data from event
        text = event.transcript if hasattr(event, 'transcript') else str(event)
        conf = event.confidence if hasattr(event, 'confidence') else 1.0
        is_final = event.is_final if hasattr(event, 'is_final') else True
        
        # Only process final transcripts
        if not is_final:
            return
        
        async with self._lock:
            speaking = self.agent_speaking
        
        logger.info(f" Transcript: '{text}' (conf={conf:.2f}, agent_speaking={speaking})")
        
        # --- CASE 1: Agent is speaking - evaluate for interrupt ---
        if speaking:
            should_interrupt = await self._evaluate_for_interrupt(text, conf)
            
            if not should_interrupt:
                logger.info(f" Blocking transcript from being processed by agent: {text!r}")
                if hasattr(event, 'handled'):
                    event.handled = True
            return
        
        # --- CASE 2: Agent NOT speaking - register ALL speech (including fillers) ---
        # When agent is quiet, even "umm", "hmm" are valid speech events
        # The session will naturally handle them
        logger.info(f" User input while agent quiet (ALL speech registered): {text!r}")