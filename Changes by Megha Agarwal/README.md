# LiveKit Voice Interruption Handling — SalesCode.ai Final Round (Megha Agarwal)

This branch implements an **extension-layer interrupt handler** for a LiveKit voice agent.  
It filters out fillers like “uh/umm/hmm/haan” **only while the agent is speaking**, but treats them as normal speech when the agent is quiet. It also prioritizes **real interrupts** like “wait/stop/hold on” with near-zero added latency.  
Challenge brief referenced from *SalesCode_AI_Final_Round_Qualifier.pdf*.

---

## What’s included

- `agent.py` — boots a LiveKit AgentSession, wires event callbacks, and configures conservative built‑ins (e.g., min words/duration for interruptions).  
- `interruption_handler.py` — `InterruptExtension` that performs runtime filtering of STT transcripts, without touching LiveKit’s VAD.

> You can read these two files for the exact wiring and settings.

---

## Key behaviors

1. **Ignore fillers when TTS is speaking**  
   If the agent is in the `speaking` state, filler‑only segments are dropped (no interruption).

2. **Register fillers when the agent is quiet**  
   When idle/listening, even “umm/hmm” are delivered as normal user speech.

3. **Immediate stop on real interrupts**  
   Keywords such as “stop”, “wait”, “hold on”, etc. trigger a stop path immediately. Ambiguous cases fall back to an LLM intent check.

4. **No VAD changes**  
   This is a pure extension; LiveKit’s base VAD is unchanged.

5. **Configurable & language‑agnostic**  
   The ignored list is configurable; confidence threshold and keyword lists are easy to extend for multilingual setups.

---

## How it works

- The session emits events for **agent state** and **user transcripts**.  
- `InterruptExtension` keeps an `agent_speaking` flag and an async lock for thread-safety.  
- On final STT transcripts:
  - **If speaking**:  
    - Drop low-confidence segments.  
    - Strip fillers and short acks (“ok/okay/yeah”).  
    - If any **strong interrupt keyword** exists → stop TTS immediately.  
    - Else, for non-trivial utterances → ask the LLM (Gemini) to classify `interrupt|ignore`.  
  - **If not speaking**: forward all speech (including fillers).

---

## Project layout

```
.
├── agent.py
└── interruption_handler.py
```

---

## Requirements

- Python 3.10+
- A running **LiveKit server** (WS URL + API key/secret)
- API keys for the plugins you enable in `agent.py`:
  - **Deepgram STT** (or switch to another STT plugin you prefer)
  - **Google Gemini** (LLM) for the fallback classification
  - **Speechify** (TTS) (or any other TTS plugin you prefer)

### Environment variables (example `.env`)

```
# LiveKit
LIVEKIT_URL=wss://<your-livekit-host>
LIVEKIT_API_KEY=<your-key>
LIVEKIT_API_SECRET=<your-secret>

# STT / LLM / TTS
DEEPGRAM_API_KEY=<your-deepgram-key>
GOOGLE_API_KEY=<your-google-key>
SPEECHIFY_API_KEY=<your-speechify-key>
# or the corresponding keys if you swap providers
```

> `agent.py` loads `.env` automatically via `python-dotenv` and constructs the session with Silero VAD, Deepgram STT, Google Gemini LLM, and Speechify TTS by default. You can swap any of these to providers you prefer.

---

## Installation

```bash
# 1) Create & activate a virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 2) Install core dependencies
pip install --upgrade pip

# LiveKit Agents & common plugins
pip install livekit-agents livekit-plugins-deepgram livekit-plugins-google             livekit-plugins-silero livekit-plugins-speechify python-dotenv
```

> If you choose different providers, install their corresponding LiveKit plugin packages.

---

## Run

```bash
# Make sure your .env is present in the project root
python agent.py
```

The script uses `cli.run_app(WorkerOptions(...))`, so it launches a worker that connects to your LiveKit room as configured by the environment. Check your terminal logs for connection and event messages.

---

## Configuration knobs

In `agent.py` (session creation):

- `allow_interruptions=True` — enable interruption handling.
- `min_interruption_words=3` — ignore very short “okay/yeah” style blips.
- `min_interruption_duration=1.2` — ignore sub-second bursts that are mostly fillers.

In `interruption_handler.py`:

- `ignored_words=[...]` — extend this with local-language fillers (e.g., “haan”).  
- `min_conf=0.6` — drop STT segments below this confidence.  
- **Fast-path keywords** — `{"stop","wait","hold on","pause","one second",...}`.  
- **Fallback LLM** — Gemini classifies ambiguous cases as `interrupt` or `ignore`.

---

## Testing the scenarios

> Open your client and start speaking while the agent talks. Watch the logs.

### 1) Filler while agent speaks
Say: “uh… hmm… umm…”  
**Expected**: Agent **continues** talking; logs show “Ignored pure filler during agent speech”.

### 2) Real interruption
Say: “wait one second”, “stop”, or “no not that one”  
**Expected**: Agent **stops immediately**; logs show “INTERRUPT DETECTED (keyword match)”.

### 3) Mixed filler + command
Say: “umm okay stop”  
**Expected**: Should **stop** (keyword fast-path).

### 4) Agent is quiet
Say: “umm”  
**Expected**: Treated as **valid** user speech; forwarded to the agent pipeline.

### 5) Low-confidence background murmur
Mutter softly: “hmm yeah” so ASR confidence is low  
**Expected**: **Ignored** if confidence < `min_conf`.

---

## Logs & observability

The extension logs both **ignored** and **accepted** paths. Look for lines like:

- `Agent state changed: speaking (agent_speaking=True)`  
- `Ignored pure filler during agent speech: 'umm hmm'`  
- `INTERRUPT DETECTED (keyword match): 'wait a minute'`  
- `Evaluating ambiguous input with Gemini: 'actually can you...'`

You can change the logger level/name in both files if needed.

---

## Known issues / trade-offs

- **ASR segmentation** varies by provider; extremely short phrases may appear as partials. The handler only processes **final** transcripts to avoid churn.
- **LLM fallback** adds a small call overhead on ambiguous cases. If you want zero LLM usage, remove the fallback and rely on keywords + rules.
- Keyword lists are **locale-sensitive**; add Hindi/vernacular equivalents as needed for your use case.

---

## How to extend (bonus ideas)

- **Runtime config**: expose an admin endpoint or websocket message to update `ignored_words` & thresholds without restart.
- **Multilingual**: maintain per-language filler/ack lists (Hindi/English mix), detect via STT metadata, then choose lists dynamically.
- **Confidence shaping**: scale `min_conf` up while speaking and down while listening.

---

## License / Notes

- Built for the SalesCode.ai qualifier. Use responsibly and attribute any borrowed snippets.
