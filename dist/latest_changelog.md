## [1.2.8] - 2026-11-21
### Dynamic AI Model Discovery & Custom Model Manager (Ollama, Gemini, OpenAI)

#### Added & Enhanced
- Dynamic Local Ollama Model Discovery: Automatically queries local Ollama (`/api/tags`) and lists all installed models (`qwen2.5-coder`, `deepseek-coder`, `llama3`, `dolphin-mistral`, `moondream`, etc.) in the AI CAD dropdown.
- Interactive `+ Model` & `➕ Add / Try New Model` Dialog: Add any custom local or cloud model on the fly across Local Ollama, Google Gemini, or OpenAI-compatible endpoints (LM Studio, OpenRouter, DeepSeek, vLLM).
- Persistent Custom Models: Custom models are saved directly to settings and persist across OrcaSlicer sessions with friendly labels.
- Custom Models Manager in Settings: View, organize, and remove experimental custom models in a dedicated settings panel.
- Arbitrary OpenAI & Gemini Model Routing: Ensured custom model identifiers selected in the dropdown pass through directly to OpenAI/Gemini chat endpoints without preset restrictions.

#### Fixed & Security
- Fixed model override precedence in OpenAI chat caller so user-selected custom models take precedence over default settings.
