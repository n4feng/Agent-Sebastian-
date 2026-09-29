"""Generate editable draw.io diagrams. Optional PNG previews require Pillow."""
import argparse
import math
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLORS = {
    "blue": ("#dae8fc", "#6c8ebf"),
    "purple": ("#e1d5e7", "#9673a6"),
    "green": ("#d5e8d4", "#82b366"),
    "red": ("#f8cecc", "#b85450"),
    "gray": ("#f5f5f5", "#b3b3b3"),
    "white": ("#ffffff", "#9ca3af"),
}


class Diagram:
    def __init__(self, name, width, height):
        self.name, self.width, self.height = name, width, height
        self.file = ET.Element("mxfile", host="app.diagrams.net", type="device")
        page = ET.SubElement(self.file, "diagram", id=name, name=name)
        model = ET.SubElement(page, "mxGraphModel", dx=str(width), dy=str(height), grid="1", gridSize="10",
                              guides="1", tooltips="1", connect="1", arrows="1", fold="1", page="1",
                              pageScale="1", pageWidth=str(width), pageHeight=str(height), math="0", shadow="0")
        self.root = ET.SubElement(model, "root")
        ET.SubElement(self.root, "mxCell", id="0")
        ET.SubElement(self.root, "mxCell", id="1", parent="0")
        self.nodes, self.edges = {}, []

    def node(self, identifier, label, x, y, width, height, color="blue", diamond=False, title=False, container=False):
        fill, stroke = COLORS[color]
        style = ("rhombus;" if diamond else "rounded=1;arcSize=12;") + (
            f"whiteSpace=wrap;html=0;fillColor={fill};strokeColor={stroke};fontColor=#172033;"
            "fontFamily=Helvetica;fontSize=16;align=center;verticalAlign=middle;spacing=8;")
        if title:
            style = "text;html=0;strokeColor=none;fillColor=none;align=left;fontSize=23;fontStyle=1;fontColor=#172033;"
        if container:
            style += "verticalAlign=top;align=left;fontSize=17;fontStyle=1;spacingTop=14;spacingLeft=16;dashed=1;"
        cell = ET.SubElement(self.root, "mxCell", id=identifier, value=label, style=style, vertex="1", parent="1")
        ET.SubElement(cell, "mxGeometry", x=str(x), y=str(y), width=str(width), height=str(height), **{"as": "geometry"})
        self.nodes[identifier] = dict(label=label, x=x, y=y, w=width, h=height, color=color,
                                       diamond=diamond, title=title, container=container)

    def edge(self, source, target, points, label="", dashed=False):
        identifier = "edge-" + str(len(self.edges))
        style = "edgeStyle=orthogonalEdgeStyle;rounded=0;html=0;endArrow=block;endFill=1;strokeColor=#526174;fontColor=#344054;fontSize=14;labelBackgroundColor=#ffffff;"
        s, t = self.nodes[source], self.nodes[target]
        style += (f"exitX={(points[0][0]-s['x'])/s['w']};exitY={(points[0][1]-s['y'])/s['h']};"
                  f"entryX={(points[-1][0]-t['x'])/t['w']};entryY={(points[-1][1]-t['y'])/t['h']};")
        if dashed:
            style += "dashed=1;"
        cell = ET.SubElement(self.root, "mxCell", id=identifier, value=label, style=style,
                            edge="1", parent="1", source=source, target=target)
        geo = ET.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})
        array = ET.SubElement(geo, "Array", **{"as": "points"})
        for x, y in points[1:-1]:
            ET.SubElement(array, "mxPoint", x=str(x), y=str(y))
        self.edges.append((points, label, dashed))

    def save(self, preview=None):
        directory = ROOT / "docs/diagrams"
        directory.mkdir(parents=True, exist_ok=True)
        ET.indent(self.file)
        ET.ElementTree(self.file).write(directory / (self.name + ".drawio"), encoding="utf-8", xml_declaration=True)
        if preview:
            self.render(preview)

    def render(self, directory):
        from PIL import Image, ImageDraw, ImageFont
        image = Image.new("RGB", (self.width, self.height), "white")
        draw = ImageDraw.Draw(image)
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        font = ImageFont.truetype(font_path, 15)
        title_font = ImageFont.truetype(font_path, 23)
        for node in self.nodes.values():
            x, y, w, h = node["x"], node["y"], node["w"], node["h"]
            fill, stroke = COLORS[node["color"]]
            if not node["title"]:
                if node["diamond"]:
                    draw.polygon([(x + w / 2, y), (x + w, y + h / 2), (x + w / 2, y + h), (x, y + h / 2)], fill=fill, outline=stroke, width=2)
                else:
                    draw.rounded_rectangle((x, y, x + w, y + h), radius=10, fill=fill, outline=stroke, width=2)
            lines = node["label"].split("\n")
            chosen_font = title_font if node["title"] else font
            if node["container"] or node["title"]:
                draw.multiline_text((x + (16 if node["container"] else 0), y + 12), node["label"], font=chosen_font, fill="#172033", spacing=5)
            else:
                bbox = draw.multiline_textbbox((0, 0), node["label"], font=chosen_font, spacing=5, align="center")
                draw.multiline_text((x + w / 2 - (bbox[2] - bbox[0]) / 2, y + h / 2 - (bbox[3] - bbox[1]) / 2 - bbox[1]),
                                    node["label"], font=chosen_font, fill="#172033", spacing=5, align="center")
        for points, label, dashed in self.edges:
            draw.line(points, fill="#526174", width=2)
            x, y = points[-1]
            px, py = points[-2]
            angle = math.atan2(y - py, x - px)
            draw.polygon([(x, y), (x - 10 * math.cos(angle) + 4 * math.sin(angle), y - 10 * math.sin(angle) - 4 * math.cos(angle)),
                          (x - 10 * math.cos(angle) - 4 * math.sin(angle), y - 10 * math.sin(angle) + 4 * math.cos(angle))], fill="#526174")
            if label:
                middle = len(points) // 2
                a, b = points[middle - 1], points[middle]
                lx, ly = (a[0] + b[0]) / 2 + 6, (a[1] + b[1]) / 2 - 18
                bbox = draw.textbbox((lx, ly), label, font=font)
                draw.rectangle((bbox[0] - 2, bbox[1] - 1, bbox[2] + 2, bbox[3] + 1), fill="white")
                draw.text((lx, ly), label, font=font, fill="#344054")
        Path(directory).mkdir(parents=True, exist_ok=True)
        image.save(Path(directory) / (self.name + ".png"))


def outbound():
    d = Diagram("outbound-control", 1000, 1210)
    d.node("title", "External request controls", 55, 20, 800, 50, title=True)
    d.node("propose", "Local agent proposes\na minimal external task", 350, 95, 300, 70)
    d.node("inspect", "Assemble exact payload\nand inspect locally", 350, 205, 300, 70)
    d.node("privacy", "Privacy\ndecision", 390, 315, 220, 120, "purple", diamond=True)
    d.node("block", "BLOCK\nNo external transmission", 40, 335, 245, 85, "red")
    d.node("review", "Human reviews exact payload\nRisk reasons and maximum cost", 315, 490, 370, 85, "purple")
    d.node("allow", "Approve\nthis request?", 395, 625, 210, 115, "purple", diamond=True)
    d.node("reject", "Reject or redact\nNew payload needs new review", 40, 640, 270, 85, "red")
    d.node("budget", "Atomically reserve cost\nRequest, task, day, month limits", 315, 790, 370, 80)
    d.node("costblock", "Insufficient allowance\nDo not dispatch", 740, 790, 220, 80, "red")
    d.node("send", "Send approved payload\nthrough the provider adapter", 330, 925, 340, 75, "green")
    d.node("settle", "Record usage and reconcile\nKeep uncertain charges reserved", 315, 1060, 370, 80, "green")
    d.edge("propose", "inspect", [(500,165),(500,205)])
    d.edge("inspect", "privacy", [(500,275),(500,315)])
    d.edge("privacy", "block", [(390,375),(285,375)], "Secret")
    d.edge("privacy", "review", [(500,435),(500,490)], "Review")
    d.edge("review", "allow", [(500,575),(500,625)])
    d.edge("allow", "reject", [(395,683),(310,683)], "No")
    d.edge("allow", "budget", [(500,740),(500,790)], "Yes")
    d.edge("budget", "costblock", [(685,830),(740,830)])
    d.edge("budget", "send", [(500,870),(500,925)], "Within cap")
    d.edge("send", "settle", [(500,1000),(500,1060)])
    return d


def architecture():
    d = Diagram("system-architecture", 1040, 1060)
    d.node("title", "Agent Sebastian on a 36 GB Mac", 45, 20, 900, 50, title=True)
    d.node("terminal-box", "Terminal container\nNo IP network and no API keys", 35, 105, 345, 550, "gray", container=True)
    d.node("gateway-box", "Gateway container\nOnly outbound network owner", 425, 105, 575, 550, "gray", container=True)
    d.node("console", "Text console\nchat  exit  setting", 75, 190, 265, 75)
    d.node("agent", "Local agent loop\nValidated tool registry", 75, 330, 265, 85)
    d.node("files", "Allowed host files\nproject read only\n../playground read and write", 75, 500, 265, 110)
    d.node("gateway", "Fixed model adapters\nValidated settings and requests", 485, 190, 455, 75)
    d.node("local", "Local inference route\nOne model and one slot", 455, 340, 230, 85)
    d.node("privacy", "Privacy screening\nHuman export approval", 740, 340, 230, 85, "purple")
    d.node("state", "Protected Docker volumes\nAPI keys and accounting", 455, 505, 230, 85, "purple")
    d.node("budget", "Budget reservations\nand usage reconciliation", 740, 505, 230, 85, "green")
    d.node("host-box", "Native macOS sandbox", 65, 720, 475, 235, "gray", container=True)
    d.node("dmr", "Docker Model Runner with Metal\nQwen3.8 / Muse Glimmer / Gemma 4\nQ4 weights and bounded KV cache", 95, 790, 415, 110, "green")
    d.node("cloud", "Commercial APIs\nOpenAI Responses\nAnthropic Messages", 690, 790, 310, 110, "green")
    d.node("note", "Inference memory is outside the Linux VM limit. Validate memory pressure on the Mac.", 55, 985, 940, 45, "white")
    d.edge("console", "agent", [(208,265),(208,330)])
    d.edge("agent", "files", [(208,415),(208,500)], "File tools")
    d.edge("agent", "gateway", [(340,372),(402,372),(402,228),(485,228)], "Unix socket")
    d.edge("gateway", "local", [(570,265),(570,340)])
    d.edge("gateway", "privacy", [(855,265),(855,340)])
    d.edge("privacy", "budget", [(855,425),(855,505)])
    d.edge("state", "budget", [(685,548),(740,548)], dashed=True)
    d.edge("local", "dmr", [(570,425),(705,425),(705,690),(300,690),(300,790)])
    d.edge("budget", "cloud", [(855,590),(855,790)], "Approved HTTPS")
    return d


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview-dir")
    options = parser.parse_args()
    for diagram in (outbound(), architecture()):
        diagram.save(options.preview_dir)
