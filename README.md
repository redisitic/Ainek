# ye sabh karna hei aaj

# 🦾 Alnek: AI-Powered Voice Assistant for Visually Impaired Users  

---

## 📌 Problem Statement  
Visually impaired users struggle with modern GUIs where screen readers fail to interpret icons, menus, and visual cues. This creates dependency on external help for tasks like browsing, emailing, or navigating files.  

---

## 🎯 Goal  
Build a lightweight **Python-based assistant** that:  
- Activates via **hotkey**  
- Captures a **screenshot + voice command**  
- Uses **AI/LLMs + Vision Models** to interpret intent  
- Gives **spoken instructions** for navigation  

---

## 🚀 Implementation Plan  

### ✅ Core MVP (Hackathon Focus)  
1. **Hotkey Activation** – listen globally for trigger (e.g., `Ctrl+Alt+S`)  
2. **Screen Capture** – grab screenshot of current screen  
3. **Voice Input** – record audio, send to Google Gemini API (STT)  
4. **AI Processing**  
   - Gemini Vision → analyze screenshot  
   - LLM (via Gemini) → interpret intent  
   - Generate action steps  
5. **Voice Output** – convert instructions to speech with TTS  

### 🔄 Optional (if time allows)  
- Cursor location feedback (`x, y` coordinates)  
- Basic error handling (`"Sorry, I didn’t understand"`)  
- Support for one extra app (Word or email client)  

## 1) Conversation / interaction

* **Ask the assistant a question or give a command** — natural language input; assistant replies (text + optional TTS).
* **Change assistant persona / name / voice** — tell it to use a different persona or voice and it will adopt that for speech and responses.
* **Play sound effects** — request short sfx when the assistant replies.

## 2) Speech & audio

* **Text-to-speech (TTS) playback** — have any assistant reply spoken aloud.
* **Speak in a character/persona** — prompt it to speak using a specified persona.
* **Short confirmations** — brief spoken acknowledgements for background tasks.

## 3) Open / control applications and windows

* **Open desktop applications by name** — ask it to open an app (e.g., browser, notepad, Word); it will either launch the executable or simulate OS search+enter.
* **Switch/focus windows** — ask to focus a particular application/window.
* **Lock workstation / shutdown commands** — (script exposes OS control actions) — can be invoked if requested.

## 4) Web browsing & searching

* **Perform a web search** — give a query; it will run a search, gather top results, speak a summary, and open the first result if asked.
* **Research a topic** — request a deeper research summary; it crawls top pages, aggregates text, then synthesizes a summary.
* **Open specific URLs or web pages** — ask to open a URL or open a search result.
* **YouTube search & play** — ask to search YouTube and open/play a selected result.

## 5) Content generation & writing

* **Generate written content** — ask for essays, emails, blog posts, code examples, explanations, etc.; it uses the LLM to produce content.
* **Write into an application** — request it to create a document and type/paste it into Notepad/Word or other targeted app.
* **Format typing behavior** — ask it to type (keystrokes/clipboard paste) with small formatting adjustments (line breaks, headings).

## 6) Email composition & sending

* **Compose email content** — provide recipient/topic and it will draft subject/body.
* **Send email via GUI automation** — it can open Gmail in browser and simulate typing/sending (or prefill compose page and copy body to clipboard as fallback).

## 7) Task & reminder management

* **Create reminders** — natural language reminders with datetime parsing (e.g., “remind me tomorrow at 9am to …”).
* **List, update, mark tasks** — add tasks, change status, list pending/completed tasks.
* **Background reminder delivery** — scheduled reminders are checked and spoken when due.

## 8) Skill learning & automation (high-level)

* **Ask the assistant to “learn” a new skill** — supply a command and it will generate a structured automation (a skill) and save it for reuse.
* **Execute learned skills** — run saved multi-step skills (open apps, type, click, browse, etc.).
* **Apply a previously learned action to new inputs** — reuse learned recipes on similar future commands.

## 9) Arbitrary automation & code execution (power user)

* **Request code generation / run generated code** — ask for code examples or to run generated Python snippets (the script can execute code strings as part of a skill).
* **Generate complex project scaffolding** — request project/code generation and background refinement (scaffolding + files).

> Note: these are powerful — they allow the assistant to run code or OS commands (treated here as “what the user can do”).

## 10) GUI automation & screen interactions

* **Locate and click on-screen images / UI elements** — tell it to click a UI element using a reference image.
* **Take screenshots** — capture screen regions and save them to disk.
* **Guide user to capture assets** — open example pages and help the user create `assets/` images by timed screenshots.

## 11) Clipboard & typing helpers

* **Copy/paste content to clipboard** — generate text and place it on clipboard for manual use.
* **Type long text automatically** — paste or type long generated text into focused applications.

## 12) File, memory & logs

* **Save and load assistant memory** — persistent memory store for tasks, reminders, learned skills, and preferences.
* **View recent interactions / logs** — read back recent command history or interaction log.
* **Export or persist skills and activity** — skills and user activity logs are stored to disk for reuse/audit.

## 13) Web scraping & data extraction

* **Scrape web page text** — visit pages and extract body text to summarize or include in responses.
* **Collect multiple search result pages** — follow and aggregate text from several top results.

## 14) Utility & miscellaneous features

* **Find similar past commands** — ask it to recall similar past actions and reapply them.
* **Retry last failed action** — instruct it to retry the most recent failed command.
* **Run background jobs** — long-running background tasks like research, project generation, or reminder monitoring.
* **Play/pause audio and control playback** — control TTS/audio playback behavior.

---

