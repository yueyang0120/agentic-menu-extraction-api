# Agentic Menu Extraction API

FastAPI service for agentic menu extraction from real-world menu photos. The API accepts an image from a mobile client, runs a vision-based extraction pipeline, and returns normalized menu items with translations, ingredients, allergens, cooking methods, dietary labels, cuisine type, and category metadata.

This project is the backend layer for a practical AI product: image input -> agentic extraction -> multilingual enrichment -> authenticated mobile API.

## Product Use Case

Travelers and diners often face menus in unfamiliar languages or formats. A useful assistant needs to do more than OCR the dish names. It should identify menu items, preserve original names, translate descriptions, infer common ingredients and allergens, and return data in a shape that a mobile app can display immediately.

Agentic Menu Extraction API focuses on that backend problem:

- accept a base64-encoded menu image;
- extract all visible menu items through a structured vision workflow;
- translate generated fields into the user's target language;
- enrich each item with dietary and allergen metadata;
- return a typed JSON response for a mobile UI; and
- protect the API with device-level request authentication.

## What It Demonstrates

- FastAPI service design for a multimodal AI product.
- OpenAI vision integration with schema-constrained structured extraction.
- Prompt constraints for target-language consistency and schema compliance.
- Pydantic models for typed extraction contracts.
- Device registration with per-device secrets.
- HMAC-based request verification with timestamp windows to reduce replay risk.
- SQLite for local device-secret storage and PostgreSQL support through `DATABASE_URL`.
- Railway-friendly deployment configuration.

## API Surface

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Health check with service status, timestamp, and version. |
| `POST /auth/register` | Registers or updates a device-specific secret. |
| `POST /analyze-menu` | Runs agentic extraction on a base64-encoded menu image and returns structured menu data. |

## Architecture

```text
Mobile client image
  -> FastAPI request validation
  -> device/HMAC authentication
  -> target-language normalization
  -> agentic vision extraction
  -> typed MenuItem response
  -> mobile UI rendering
```

The backend keeps the mobile client thin. The app captures or selects an image, sends the base64 payload to the backend, and receives normalized menu entities. The backend owns prompt construction, language policy, authentication checks, structured extraction, and schema validation.

## Authentication Model

The preferred authentication path uses timestamped HMAC headers:

- `X-Device-Id`
- `X-Timestamp`
- `X-Signature`
- `Authorization: Bearer v1`

Each device registers a device-specific secret. Requests are signed with that secret and validated inside a configurable timestamp window (`AUTH_WINDOW_SECONDS`). A legacy bearer-token path remains for backward compatibility, but the dynamic device-auth path is the intended model.

## Configuration

Set these values in a local `.env` file or deployment secret manager. Do not commit real secrets.

```env
OPENAI_API_KEY=...
BACKEND_API_SECRET=...
MASTER_SECRET=...
AUTH_WINDOW_SECONDS=300
MAX_OUTPUT_TOKENS=16000
DATABASE_URL=...
```

`DATABASE_URL` is optional. Without it, the service uses local SQLite (`devices.db`) for device registration.

## Local Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload
```

Then open:

```text
http://127.0.0.1:8000/health
```

## Portfolio Positioning

This project demonstrates backend execution for an applied AI product: API design, typed schemas, multimodal model integration, prompt constraints, deployment configuration, and mobile-oriented authentication.

The strongest positioning is not "menu scanner demo"; it is an agentic extraction service that converts messy real-world visual input into structured, localized data that a user-facing product can rely on.
