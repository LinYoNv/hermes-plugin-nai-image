---
name: nai-anime-illustration
description: NovelAI V4.5 & V5 anime image generation with built-in 19,663-entry Grimoire, style slots, and multi-character blocks
---

Use `nai_generate_image` when the user asks you to draw, generate an anime illustration, take a selfie, or send a picture.

### Workflow & Best Practices
1. **Grimoire Search First (`nai_search_grimoire`)**:
   - When the scene involves specific outfits (e.g. `旗袍`, `死库水`, `洛丽塔`, `女仆装`), poses/angles (`自拍`, `回眸`, `俯视`), or lighting/atmosphere (`夜景`, `逆光`), call `nai_search_grimoire(query="...")` first to retrieve battle-tested NovelAI V4.5/V5 tag combinations, OR embed `【法典:词条名】` directly inside `prompt`.
2. **Tag Formatting (`prompt`)**:
   - Write comma-separated English Danbooru/NovelAI tags.
   - Put quality tags at the front: `masterpiece, best quality, very aesthetic, absurdres`.
   - Weight syntax: `1.3::important tag::` to boost, `-2::unwanted element::` to suppress, or `{tag}` / `[tag]`.
3. **Dual-Character Scenes (`char1_prompt` & `char2_prompt`)**:
   - When drawing two characters, keep scene/background/composition tags in `prompt`, put Character 1's appearance/outfit/pose in `char1_prompt`, and Character 2's in `char2_prompt`. This prevents appearance/hair/eye color bleed between characters.
   - Use `source#action` (initiator) and `target#action` (receiver) for interactions (e.g. `source#hug`, `target#hug`).
4. **Delivery**:
   - Include the returned `media_tag` (`MEDIA:/path/to/image.png`) on its own line in your reply alongside a natural in-character message so the user receives the image directly.
