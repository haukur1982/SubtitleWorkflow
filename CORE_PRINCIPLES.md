# Omega Pro: Core Principles & Vision

> *"Build something worthy of the message it carries."*

---

## The Mission

Omega Pro exists to deliver **world-class subtitles** to audiences who deserve to understand life-changing content in their own language. Every sermon, every teaching, every message processed through this system will be read by people seeking truth, hope, and wisdom.

**The subtitles must be invisible** - so natural, so accurate, so beautifully timed that viewers forget they're reading and simply *experience* the message.

---

## Core Principles

### 1. 🎯 ACCURACY IS SACRED
- The message of the speaker is paramount
- Theological precision matters (names, terms, meaning)
- Better to flag uncertainty than to guess wrong
- The AI serves the content, not the other way around

### 2. ⚡ INSTANT & RESPONSIVE
- Zero perceived lag in the user interface
- Every click, every tab feels immediate
- The operator should never wait on the system
- Real-time feedback on what's happening

### 3. 🏗️ ROCK-SOLID FOUNDATION
- The system runs 24/7 without babysitting
- Handle 30 programs in the queue without choking
- Process jobs sequentially, never overwhelm resources
- Graceful recovery from any failure

### 4. 🌊 FLOW LIKE WATER
- Components talk to each other efficiently
- No unnecessary re-fetching or re-processing
- State flows from one source of truth
- Data architecture is clean and predictable

### 5. 🚀 THINK BIG, BUILD CAREFULLY
- Dream of features that set us apart
- But implement with engineering discipline
- Every new feature must not break existing stability
- Quality over speed, always

---

## Quality Standards

### For Subtitles
- **Timing**: Natural reading pace, never rushed
- **Phrasing**: Sounds like a native speaker wrote it
- **Accuracy**: True to source meaning and theological intent
- **Consistency**: Same terms used the same way throughout
- **Broadcast-ready**: Meets professional TV standards

### For the System
- **Reliability**: Can be left running overnight
- **Scalability**: Handles 10, 20, 30+ programs gracefully
- **Observability**: Always know what the system is doing
- **Recoverability**: Any failure can be retried/recovered

### For the User Experience
- **Instant**: Tab switches < 50ms
- **Informative**: See translation progress in real-time
- **Controllable**: Human-in-the-loop for quality checks
- **Professional**: UI worthy of DaVinci Resolve

---

## The Three Users

1. **The Operator (You)**
   - Needs efficiency, clarity, control
   - Should feel like driving a high-performance vehicle
   - Never frustrated by lag, confusion, or errors

2. **The Viewer (Audience)**
   - Needs subtitles that feel natural
   - Should forget they're reading subtitles
   - Experiences the message, not the translation

3. **The Speaker (Content Creator)**
   - Needs their message faithfully conveyed
   - Theological and emotional intent preserved
   - Their voice, in another language

---

## Technical Guardrails

### Throughput Management
- ✅ One job per stage at a time
- ✅ Queue-based processing (not parallel overload)
- ✅ System health checks before starting new work
- ✅ Graceful backpressure when resources are constrained

### Architecture Rules
- State lives in Zustand store (frontend)
- SSE pushes updates (no polling)
- Backend is the source of truth for jobs
- Cloud handles AI-heavy work (translation, transcription)
- Local handles video-heavy work (encoding, burn)

---

## The Vision

A system so reliable and pleasant that:
- You can drop 30 programs and walk away
- Every morning, finished work appears ready for delivery
- Quality is consistent and broadcast-ready
- New features enhance without destabilizing
- It becomes the standard for subtitle production

---

## Decision Framework

When facing any decision, ask:

1. **Does this serve the message?** (Accuracy/quality)
2. **Does this improve reliability?** (Stability)
3. **Does this make the operator's life easier?** (UX)
4. **Does this scale?** (Throughput)
5. **Is the foundation solid before adding features?** (Discipline)

If a feature conflicts with stability, **stability wins**.
If a shortcut conflicts with quality, **quality wins**.

---

*This document is the North Star. Return here when in doubt.*
