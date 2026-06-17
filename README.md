---
title: Noneegsearch
emoji: 🌍
colorFrom: red
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---

# Proactive Search

Search PubMed for an author's publications and surface those linked to their
institution via ROR. Uses spaCy + GLiNER for affiliation parsing.

## Models

Models are baked into the Docker image at build time:

- spaCy: `en_core_web_sm` (installed via pip wheel)
- GLiNER: `urchade/gliner_medium-v2.1` (downloaded during image build)

No runtime download — the app boots with models ready to use.

## Local development

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy

Push to a Hugging Face Space configured with `sdk: docker`. The image build
will preload all models. Cold start is just loading weights into memory.

Configuration reference: https://huggingface.co/docs/hub/spaces-config-reference
