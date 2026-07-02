from __future__ import annotations

import html
import zipfile
from pathlib import Path

from ppt_agent.assembly.layout_fit import bullet_font_size, title_font_size

SLIDE_W = 12192000
SLIDE_H = 6858000


def _emu(position: list[float]) -> tuple[int, int, int, int]:
    x, y, w, h = position
    return int(x * SLIDE_W), int(y * SLIDE_H), int(w * SLIDE_W), int(h * SLIDE_H)


def _text_runs(text: str, size: int = 20, bullet: bool = False) -> str:
    escaped = html.escape(text)
    bu = '<a:buChar char="•"/>' if bullet else "<a:buNone/>"
    return (
        f"<a:p><a:pPr marL=\"0\" indent=\"0\">{bu}</a:pPr>"
        f"<a:r><a:rPr lang=\"zh-CN\" sz=\"{size * 100}\" dirty=\"0\"/><a:t>{escaped}</a:t></a:r>"
        "<a:endParaRPr lang=\"zh-CN\"/></a:p>"
    )


def _shape(shape_id: int, name: str, position: list[float], body_xml: str) -> str:
    x, y, w, h = _emu(position)
    return f"""
      <p:sp>
        <p:nvSpPr>
          <p:cNvPr id="{shape_id}" name="{html.escape(name)}"/>
          <p:cNvSpPr txBox="1"/>
          <p:nvPr/>
        </p:nvSpPr>
        <p:spPr>
          <a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{w}" cy="{h}"/></a:xfrm>
          <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
          <a:noFill/><a:ln><a:noFill/></a:ln>
        </p:spPr>
        <p:txBody>
          <a:bodyPr wrap="square" rtlCol="0"/>
          <a:lstStyle/>
          {body_xml}
        </p:txBody>
      </p:sp>
    """


def _placeholder(shape_id: int, position: list[float]) -> str:
    x, y, w, h = _emu(position)
    return f"""
      <p:sp>
        <p:nvSpPr><p:cNvPr id="{shape_id}" name="Visual placeholder"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
        <p:spPr>
          <a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{w}" cy="{h}"/></a:xfrm>
          <a:prstGeom prst="roundRect"><a:avLst/></a:prstGeom>
          <a:solidFill><a:srgbClr val="EEF2F6"/></a:solidFill>
          <a:ln w="12700"><a:solidFill><a:srgbClr val="B8C2CC"/></a:solidFill></a:ln>
        </p:spPr>
        <p:txBody><a:bodyPr/><a:lstStyle/>{_text_runs("Visual placeholder", 16)}</p:txBody>
      </p:sp>
    """


def _slide_xml(slide: dict, slide_no: int) -> str:
    shapes = []
    shape_id = 2
    title = ""
    bullets: list[str] = []
    for zone in slide.get("zones", []):
        if zone.get("type") == "title":
            title = str(zone.get("content") or "")
            shapes.append(_shape(shape_id, "Title", zone["position"], _text_runs(title, title_font_size(title))))
            shape_id += 1
        elif zone.get("type") == "bullets":
            content = zone.get("content") or []
            bullets = content if isinstance(content, list) else [str(content)]
            body = "".join(_text_runs(item, bullet_font_size(bullets), bullet=True) for item in bullets)
            shapes.append(_shape(shape_id, "Bullets", zone["position"], body or _text_runs("")))
            shape_id += 1
        elif zone.get("type") == "image":
            shapes.append(_placeholder(shape_id, zone["position"]))
            shape_id += 1
    if not title:
        shapes.append(_shape(shape_id, "Title", [0.08, 0.08, 0.84, 0.14], _text_runs(f"Slide {slide_no}", 30)))
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:bg><p:bgPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>
    <p:spTree>
      <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
      <p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
      {''.join(shapes)}
    </p:spTree>
  </p:cSld>
  <p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>"""


def _content_types(slide_count: int) -> str:
    slides = "\n".join(
        f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        for i in range(1, slide_count + 1)
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
  {slides}
</Types>"""


def _presentation_xml(slide_count: int) -> str:
    ids = "\n".join(f'<p:sldId id="{255+i}" r:id="rId{i}"/>' for i in range(1, slide_count + 1))
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:sldIdLst>{ids}</p:sldIdLst>
  <p:sldSz cx="{SLIDE_W}" cy="{SLIDE_H}" type="wide"/>
  <p:notesSz cx="6858000" cy="9144000"/>
</p:presentation>"""


def _presentation_rels(slide_count: int) -> str:
    rels = "\n".join(
        f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i}.xml"/>'
        for i in range(1, slide_count + 1)
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>"""


def write_pptx(slide_contents: dict, output_path: Path) -> None:
    slides = slide_contents.get("slides", [])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types(len(slides)))
        zf.writestr("_rels/.rels", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/></Relationships>""")
        zf.writestr("ppt/presentation.xml", _presentation_xml(len(slides)))
        zf.writestr("ppt/_rels/presentation.xml.rels", _presentation_rels(len(slides)))
        zf.writestr("docProps/core.xml", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>PPT Generation Agent Output</dc:title></cp:coreProperties>""")
        zf.writestr("docProps/app.xml", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>PPT Generation Agent</Application></Properties>""")
        for index, slide in enumerate(slides, start=1):
            zf.writestr(f"ppt/slides/slide{index}.xml", _slide_xml(slide, index))
            zf.writestr(f"ppt/slides/_rels/slide{index}.xml.rels", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>""")
