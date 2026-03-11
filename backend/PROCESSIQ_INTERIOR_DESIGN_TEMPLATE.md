# ProcessIQ Excel — Interior Design Scene Analysis (Template 28)

Use this prompt in the ProcessIQ Excel sheet for **Template No = 28** (or whatever ID you set in `INTERIOR_DESIGN_ANALYSIS_TEMPLATE_ID`).

The API sends one room/interior image and expects the LLM to return **only** valid JSON in the structure below.

---

## Prompt to paste in Excel

```
You are an expert interior design analyst. Analyze the provided room/interior image in detail.

Return ONLY valid JSON (no markdown code fences, no explanation). Use this exact structure:

{
  "scene": {
    "style": ["e.g. Japandi", "Minimalist", "Wabi-Sabi"],
    "primary_palette": [
      {"hex": "#E7D9C4", "name": "warm beige"},
      {"hex": "#CBB89A", "name": "sand beige"}
    ],
    "lighting": {
      "type": "e.g. natural daylight + pendant lamp",
      "temperature": "e.g. warm neutral ~3500K"
    }
  },
  "objects": [
    {
      "object": "e.g. sectional_sofa",
      "category": "furniture|flooring|lighting|built_in|decor|textile|plant|architectural_feature",
      "dimensions_cm": { "length": 260, "depth": 95, "height": 70 },
      "material": "e.g. wood internal frame + foam",
      "fabric": "if upholstery e.g. linen blend",
      "texture": "e.g. soft woven, smooth",
      "finish": "matte or gloss",
      "color": { "hex": "#E6D6BE", "name": "cream beige" },
      "placement": "e.g. center seating area",
      "style": "optional"
    }
  ]
}

For every object visible in the image (furniture, rugs, lights, floor, walls, shelves, plants, decor, textiles):
- Estimate real-world dimensions in cm.
- Provide color as hex and name.
- Include material, fabric (if applicable), texture, finish (matte/gloss).
- For flooring: specify tiles/wood/microcement, texture, gloss/matt, color.
- For lighting: include bulb type and temperature if visible.
- For decor with multiple items: use "materials" and "colors" arrays where needed.

Include all details an interior designer would need for specification and sourcing.
```

---

## API usage

- **Endpoint:** `POST /profile/analyse/interior-design`
- **Body:** `multipart/form-data` with:
  - `image` (file, required): room/interior image (JPEG, PNG, WebP, GIF)
  - `template_id` (number, optional): ProcessIQ template ID; defaults to `INTERIOR_DESIGN_ANALYSIS_TEMPLATE_ID` (27)
- **Response:** `{ "success": true, "filename": "...", "template_used": 28, "scene": { ... }, "objects": [ ... ] }`
