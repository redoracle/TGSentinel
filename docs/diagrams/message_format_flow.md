# Message Format Flowchart

The **message format system** is the single source of truth for every delivery path (DM alerts, Saved Messages, Digests, and Webhooks). This document explains how the renderer, the UI/editor, and the delivery payloads cooperate to ensure `{*_line}` variables behave identically regardless of preview/test vs. production send.

## Architecture

The message format system consists of three core components:

- **FormatterContext** (`message_formats/context.py`): Unified context builder for both preview and production rendering
- **FormatRegistry** (`message_formats/registry.py`): Centralized template metadata with auto-discovery of variables
- **LineBuildResult** (`message_formats/line_builder.py`): Optional diagnostic mode for debugging template rendering

## Flow

```mermaid
flowchart LR
    Worker["phase1 worker<br/>(alerts/interests)"]
    Delivery["orchestrate_delivery"]
    Notifier["Notifier (DM / save / webhook)"]
    Context["FormatterContext"]
    Renderer["message_formats.renderer"]
    LineBuilder["line_builder +<br/>LineBuildResult"]
    Registry["FormatRegistry<br/>GLOBAL_REGISTRY"]
    UI["/developer/message-formats preview"]
    Docs["docs/MESSAGE_FORMATS.md"]

    Worker --> Delivery
    Delivery --> Notifier
    Notifier --> Context
    Context --> Renderer
    Renderer --> LineBuilder
    LineBuilder --> Renderer
    UI -->|FormatterContext.from_sample()| Context
    UI -->|validate_template()| Registry
    Registry -->|FormatSpec + VariableSpec| UI
    Docs -->|mirrors templates| Registry
```

### Key points

- The worker builds a `DeliveryPayload` with `score`, `semantic_score`, `keyword_score`, `profile_name`, `reactions`, `is_vip`, etc., and passes it into `orchestrate_delivery`.
- `orchestrate_delivery` routes to the correct notifier method (DM vs. Saved vs. Digest vs. Webhook) which delegates to the renderer functions defined in `src/tgsentinel/message_formats/renderer.py`.
- **FormatterContext** provides unified context building via `from_payload()` for production and `from_sample()` for preview, eliminating duplicate logic.
- **FormatRegistry** (`GLOBAL_REGISTRY`) provides centralized template metadata, variable discovery, and template validation via `validate_template()`.
- `build_formatted_line_values` formats every `*_line` variable without adding newlines so templates control spacing, and `apply_formatted_lines` writes them once even if the template skips them.
- **LineBuildResult** with `build_with_diagnostics()` provides detailed diagnostics for each variable (source value, whether it rendered, etc.) for debugging.
- The developer UI (`/developer/message-formats`) uses `FormatterContext.from_sample()` and `FormatRegistry.validate_template()` ensuring what you preview is what you send.
- Documentation (`docs/MESSAGE_FORMATS.md`) mirrors the templates and variable lists from FormatRegistry.

## Module Structure

```text
src/tgsentinel/message_formats/
├── __init__.py          # Package exports
├── context.py           # FormatterContext - unified context builder
├── defaults.py          # SAMPLE_DATA and DEFAULT_FORMATS
├── formatter.py         # Template rendering engine
├── line_builder.py      # *_line generation + LineBuildResult diagnostics
├── registry.py          # FormatRegistry + GLOBAL_REGISTRY
└── storage.py           # YAML persistence layer
```

## API Endpoints

- `GET /api/message-formats` — Get current templates
- `PUT /api/message-formats` — Update templates
- `POST /api/message-formats/preview` — Preview with FormatterContext
- `GET /api/message-formats/registry` — Get FormatRegistry metadata
