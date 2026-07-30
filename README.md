# Mcode — Desktop AI Agent with Live2D Avatar

**Mcode** is a desktop AI agent with a Live2D virtual catgirl avatar.
It features long-term memory, file operations, command execution,
TTS speech synthesis, and emotion-aware Live2D animation.

## Core Features

| Feature | Description |
|---------|-------------|
| Long-term Memory | Mem0 + ChromaDB, cross-session memory |
| Desktop Live2D | Electron 3-window shell, Prometheus Avatar |
| File Operations | Read/write/list/delete/move via MCP tools |
| Command Execution | Whitelisted commands with sandbox protection |
| Streaming Chat | SSE streaming + ReAct Agent multi-step planning |
| Tool Calling | 20+ MCP tools, OpenAI Function Calling format |
| TTS Speech | Edge TTS (8 Chinese voices, free) |
| Emotion Detection | LLM-based emotion -> Live2D expression + lip-sync |
| Persona System | persona.md persistent personality + mood tracking |

## Architecture

Three-process architecture:

1. **Electron Desktop Shell** (Node.js) — transparent always-on-top Live2D windows
2. **Python Backend** (FastAPI :8866) — AI chat, memory, 20 MCP tools, TTS
3. **Live2D Renderer** (Node.js :8850) — model rendering + WebSocket commands

## Quick Start

```bash
pip install -r requirements.txt
cd live2d_mcp && npm install && cd ..
npm install
# Edit config/model_config.json with your API key
python main.py
```

## MCP Tools (20 total)

| Category     | Tools |
|-------------|-------|
| File Ops    | list_directory, read_text_file, read_json_file, read_yaml_file, write_file, delete_file, move_file |
| Command     | execute_command (whitelisted + sandbox) |
| System      | get_current_time, get_system_info, get_env_var |
| Network     | web_search (DuckDuckGo), download_file |
| Desktop     | clipboard_read, clipboard_write |
| Live2D      | live2d_motion |
| Agent       | think, ask_user |
| Persona     | read_persona, update_persona |

## Project Structure

```
Mcode/
├── main.py                  # Entry point
├── requirements.txt          # Python dependencies
├── package.json              # Node.js dependencies (Electron)
├── config/
│   ├── api_config.py        # API key loader (3-model separation)
│   ├── settings.py          # Global settings
│   ├── model_config.json    # Provider config (local, gitignored)
│   └── model_config.example.json
├── src/
│   ├── core/                # Business logic
│   │   ├── chat.py          # Dialogue generation engine
│   │   ├── agent.py         # ReAct Agent loop
│   │   ├── memory.py        # Mem0 memory engine
│   │   ├── state.py         # TaskContext / StepRecord
│   │   ├── sentence_splitter.py  # Sentence splitting utility
│   │   └── tool_desc.py     # Tool name -> description mapping
│   ├── infra/               # Infrastructure
│   │   ├── client.py        # OpenAI-compatible API client
│   │   ├── messages.py      # Message builder + chat history
│   │   ├── mcp_client.py    # MCP client (stdio transport)
│   │   ├── mcp_server.py    # MCP tool server (~20 tools)
│   │   └── live2d_action.py # LLM emotion detection
│   ├── tools/
│   │   └── mcp_tools.py     # MCP -> OpenAI Function Calling adapter
│   ├── tts/
│   │   ├── engine.py        # Abstract TTS engine
│   │   └── edge_tts.py      # Edge TTS implementation (8 voices)
│   ├���─ ui/
│   │   ├── web.py           # FastAPI App factory + run_web()
│   │   ├── agent_launcher.py # Process launcher (Electron/Vite/Live2D)
│   │   └── routes/          # Route modules (split by function)
│   │       ├── shared.py    # Shared state + TTS helpers
│   │       ├── chat.py      # POST /chat (SSE streaming + heartbeat)
│   │       ├── pet.py       # /pet/status, /pet/command, /pet/presets
│   │       ├── config_routes.py  # /api/model-config hot-reload
│   │       ��── tts.py       # POST /tts
│   │       ├���─ emotion.py   # POST /emotion
│   │       └── history.py   # /api/chat/history CRUD
│   └── utils/
│       ├── tokens.py        # Token counting (tiktoken)
│       └── live2d_constants.py  # Live2D semantic constants
├── electron/                # Electron desktop shell
│   ├── main.js              # Main process (3-window + IPC)
│   ├── preload.js           # Pet window preload bridge
│   └── chat_preload.js      # Chat window preload bridge
├── live2d/                  # Live2D frontend assets
│   └── model/
│       ├── action_bar.html  # Button bar page
│       ├── chat_panel.html  # Chat panel page
│       ├── settings.html    # Settings panel page
│       └── resources/       # Live2D character models (Haru / MO)
├── live2d_mcp/              # Live2D MCP render service (Node.js)
│   ├── server.js            # MCP Server + WebSocket Bridge + Express
│   └── src/                 # Browser rendering entry
└── data/                    # Runtime data (gitignored)
    ├── persona.md            # AI persona (personality/mood/affinity/events)
    ├── conversation_history.json  # Chat history persistence
    ├── hf_cache/             # HuggingFace embedding model cache (~90MB)
    ├── mem0_db/              # ChromaDB vector database
    ├── Workspace/            # File operation sandbox
    └── tts_cache/            # TTS audio cache
```

## Dependencies & Credits

| Project | Purpose |
|----------|---------|
| [Mem0](https://github.com/mem0ai/mem0) | Long-term memory engine |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | MCP protocol implementation |
| [ChromaDB](https://www.trychroma.com/) | Vector database |
| [sentence-transformers](https://github.com/UKPLab/sentence-transformers) | Local embedding model |
| [edge-tts](https://github.com/rany2/edge-tts) | Free Microsoft neural TTS |
| [FastAPI](https://fastapi.tiangolo.com/) | High-performance web framework |
| [Electron](https://www.electronjs.org/) | Desktop shell framework |
| [PIXI.js](https://pixijs.com/) | WebGL rendering engine |

## License

MIT License

## Links

- GitHub: https://github.com/YIBI2333/Mcode
- Author: YIBI2333
