import json as json_lib
from pathlib import Path
from typing import Optional, Dict


def format_size(bytes_size: Optional[int]) -> str:
    if bytes_size is None:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(bytes_size)
    unit_idx = 0
    while size >= 1024 and unit_idx < len(units) - 1:
        size /= 1024
        unit_idx += 1
    if unit_idx == 0:
        return f"{int(size)} {units[unit_idx]}"
    return f"{size:.2f} {units[unit_idx]}"


def build_tree(root_path: str, data: Dict) -> Dict:
    return data


def save_json(data: Dict, filepath: str) -> None:
    output_path = Path(filepath)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json_lib.dump(data, f, indent=2, ensure_ascii=False)


def save_html(html_path: str, json_path: str, tree: Dict) -> None:
    json_data = json_lib.dumps(tree, ensure_ascii=False, indent=2)

    html_template = r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FileAnalyser - Sunburst</title>
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #1a1a2e; color: #eee; overflow: hidden; }
        #chart-container { width: 100vw; height: 100vh; display: flex; align-items: center; justify-content: center; }
        #info { position: fixed; top: 20px; left: 20px; background: rgba(0,0,0,0.7); padding: 15px 20px; border-radius: 10px; font-size: 14px; line-height: 1.6; z-index: 100; display: none; }
        #info h2 { font-size: 18px; margin-bottom: 10px; color: #fff; }
        #info .size { font-size: 24px; font-weight: bold; color: #ffd700; }
        #info .path-text { color: #aaa; font-size: 12px; margin-top: 5px; word-break: break-all; }
        #info .type { color: #4fc3f7; }
        #info .close-btn { position: absolute; top: 10px; right: 15px; background: none; border: none; color: #888; font-size: 20px; cursor: pointer; }
        #info .close-btn:hover { color: #fff; }
        #legend { position: fixed; bottom: 20px; left: 20px; background: rgba(0,0,0,0.7); padding: 15px; border-radius: 10px; max-height: 300px; overflow-y: auto; z-index: 100; }
        #legend h3 { font-size: 14px; margin-bottom: 10px; color: #fff; }
        #legend .entry { display: flex; align-items: center; margin: 5px 0; font-size: 12px; }
        #legend .color { width: 12px; height: 12px; border-radius: 2px; margin-right: 8px; flex-shrink: 0; }
        #controls { position: fixed; top: 20px; right: 20px; background: rgba(0,0,0,0.7); padding: 15px; border-radius: 10px; z-index: 100; }
        #controls button { display: block; margin: 5px 0; padding: 8px 16px; background: #4fc3f7; border: none; border-radius: 5px; color: #000; cursor: pointer; font-size: 12px; }
        #controls button:hover { background: #81d4fa; }
        #breadcrumb { position: fixed; top: 20px; left: 50%; transform: translateX(-50%); background: rgba(0,0,0,0.7); padding: 10px 20px; border-radius: 10px; font-size: 12px; z-index: 100; max-width: 60vw; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        #breadcrumb .sep { color: #555; margin: 0 5px; }
        #breadcrumb .crumb { color: #ffd700; cursor: pointer; }
        #breadcrumb .crumb:hover { text-decoration: underline; }
        #breadcrumb .root { color: #aaa; cursor: pointer; }
        #breadcrumb .root:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <div id="chart-container"></div>
    <div id="breadcrumb">
        <span class="root" onclick="resetZoom()">Root</span>
    </div>
    <div id="info">
        <button class="close-btn" onclick="closeInfo()">&times;</button>
        <h2 id="info-name">-</h2>
        <div class="size" id="info-size">-</div>
        <div class="type" id="info-type">-</div>
        <div class="path-text" id="info-path">-</div>
    </div>
    <div id="legend">
        <h3>File Types</h3>
        <div id="legend-items"></div>
    </div>
    <div id="controls">
        <button onclick="toggleLabels()">Toggle Labels</button>
        <button onclick="exportPNG()">Export PNG</button>
    </div>

    <script>
        const treeData = JSON_DATA_PLACEHOLDER;
        let showLabels = true;
        let zoomStack = [];

        function formatSize(bytes) {
            if (bytes === 0) return "0 B";
            const units = ["B", "KB", "MB", "GB", "TB"];
            let idx = 0;
            let size = bytes;
            while (size >= 1024 && idx < units.length - 1) {
                size /= 1024;
                idx++;
            }
            return size.toFixed(idx === 0 ? 0 : 2) + " " + units[idx];
        }

        function getColor(name, index) {
            const colors = [
                '#ff6b6b', '#4ecdc4', '#45b7d1', '#96ceb4', '#ffeaa7',
                '#dfe6e9', '#fd79a8', '#a29bfe', '#6c5ce7', '#00b894',
                '#e17055', '#74b9ff', '#a3e635', '#f97316', '#8b5cf6',
                '#06b6d4', '#ec4899', '#84cc16', '#14b8a6', '#f43f5e'
            ];
            return colors[index % colors.length];
        }

        let svg, arc, root, hierarchy;
        const width = Math.min(window.innerWidth, window.innerHeight) * 0.9;
        const radius = width / 2;

        function renderSunburst(data) {
            const container = document.getElementById('chart-container');
            container.innerHTML = '';

            svg = d3.select("#chart-container")
                .append("svg")
                .attr("width", width)
                .attr("height", width)
                .append("g")
                .attr("transform", "translate(" + width/2 + "," + width/2 + ")");

            arc = d3.arc()
                .startAngle(d => d.x0)
                .endAngle(d => d.x1)
                .padAngle(0.01)
                .padRadius(radius / 2)
                .innerRadius(d => d.y0 * radius / 3)
                .outerRadius(d => Math.max(d.y0 * radius / 3, d.y1 * radius / 3 - 1));

            root = d3.hierarchy(data)
                .sum(d => d.type === 'file' ? d.size : 0)
                .sort((a, b) => b.value - a.value);

            hierarchy = d3.partition()
                .size([2 * Math.PI, 3])
                (root);

            const colorMap = {};
            let colorIdx = 0;
            root.each(d => {
                if (!colorMap[d.data.name]) {
                    colorMap[d.data.name] = getColor(d.data.name, colorIdx++);
                }
            });

            const path = svg.selectAll("path")
                .data(hierarchy.descendants().filter(d => d.depth > 0))
                .join("path")
                .attr("d", arc)
                .attr("fill", d => {
                    let node = d;
                    while (node.depth > 1 && node.parent) node = node.parent;
                    return colorMap[node.data.name] || '#888';
                })
                .attr("fill-opacity", d => d.depth === 1 ? 1 : 0.7)
                .attr("stroke", "#1a1a2e")
                .attr("stroke-width", 1)
                .style("cursor", "pointer")
                .on("click", clicked)
                .on("mouseover", showInfo)
                .on("mouseout", hideInfo);

            path.append("title")
                .text(d => d.data.name + " - " + formatSize(d.value));

            if (showLabels) {
                svg.selectAll("text")
                    .data(hierarchy.descents().filter(d => d.depth === 1))
                    .join("text")
                    .attr("transform", d => "translate(" + arc.centroid(d) + "), rotate(" + ((d.x0 + d.x1) / 2 * 180 / Math.PI - 90) + ")")
                    .attr("dy", "0.35em")
                    .attr("text-anchor", "middle")
                    .attr("fill", "#000")
                    .attr("font-size", "10px")
                    .text(d => d.data.name.length > 12 ? d.data.name.slice(0,10) + ".." : d.data.name);
            }

            updateLegend(data);
        }

        function clicked(event, p) {
            zoomStack.push(p);
            updateBreadcrumb();
            svg.selectAll("path")
                .transition()
                .duration(500)
                .attr("fill-opacity", d => isAncestorOrSelf(p, d) ? 1 : 0.2);

            svg.selectAll("text").remove();

            if (showLabels) {
                svg.selectAll("text")
                    .data(hierarchy.descents().filter(d => d.depth === 1 && isAncestorOrSelf(p, d)))
                    .join("text")
                    .attr("transform", d => "translate(" + arc.centroid(d) + "), rotate(" + ((d.x0 + d.x1) / 2 * 180 / Math.PI - 90) + ")")
                    .attr("dy", "0.35em")
                    .attr("text-anchor", "middle")
                    .attr("fill", "#000")
                    .attr("font-size", "10px")
                    .text(d => d.data.name.length > 12 ? d.data.name.slice(0,10) + ".." : d.data.name);
            }
        }

        function isAncestorOrSelf(p, d) {
            while (d.depth > 1 && d.parent) d = d.parent;
            return d === p;
        }

        function resetZoom() {
            zoomStack = [];
            updateBreadcrumb();
            svg.selectAll("path")
                .transition()
                .duration(500)
                .attr("fill-opacity", d => d.depth === 1 ? 1 : 0.7);
        }

        function updateBreadcrumb() {
            const bc = document.getElementById('breadcrumb');
            let html = '<span class="root" onclick="resetZoom()">Root</span>';
            for (let i = 0; i < zoomStack.length; i++) {
                html += '<span class="sep">/</span><span class="crumb" onclick="navigateTo(' + i + ')">' + zoomStack[i].data.name + '</span>';
            }
            bc.innerHTML = html;
        }

        function navigateTo(index) {
            zoomStack = zoomStack.slice(0, index + 1);
            updateBreadcrumb();
            const p = zoomStack[zoomStack.length - 1];
            svg.selectAll("path")
                .transition()
                .duration(500)
                .attr("fill-opacity", d => isAncestorOrSelf(p, d) ? 1 : 0.2);
        }

        function showInfo(event, p) {
            const info = document.getElementById('info');
            document.getElementById('info-name').textContent = p.data.name;
            document.getElementById('info-size').textContent = formatSize(p.value);
            document.getElementById('info-path').textContent = p.data.path || '-';
            document.getElementById('info-type').textContent = 'Type: ' + (p.data.type === 'dir' ? 'Folder' : 'File') + (p.data.type === 'file' && p.data.extension ? ' (' + p.data.extension + ')' : '');
            info.style.display = 'block';
        }

        function hideInfo() {
            document.getElementById('info').style.display = 'none';
        }

        function closeInfo() {
            document.getElementById('info').style.display = 'none';
        }

        function toggleLabels() {
            showLabels = !showLabels;
            renderSunburst(treeData);
        }

        function exportPNG() {
            const svgEl = document.querySelector('#chart-container svg');
            const serializer = new XMLSerializer();
            const svgStr = serializer.serializeToString(svgEl);
            const canvas = document.createElement('canvas');
            const ctx = canvas.getContext('2d');
            const img = new Image();
            canvas.width = svgEl.clientWidth * 2;
            canvas.height = svgEl.clientHeight * 2;
            ctx.fillStyle = '#1a1a2e';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            img.onload = function() {
                ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
                const a = document.createElement('a');
                a.download = 'sunburst.png';
                a.href = canvas.toDataURL('image/png');
                a.click();
            };
            img.src = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svgStr)));
        }

        function updateLegend(data) {
            const legend = document.getElementById('legend-items');
            const types = getFileTypes(data);
            let html = '';
            let idx = 0;
            const sorted = Object.entries(types).sort((a, b) => b[1] - a[1]).slice(0, 10);
            for (const [type, count] of sorted) {
                html += '<div class="entry"><div class="color" style="background:' + getColor(type, idx++) + '"></div>' + type + ': ' + count + ' files</div>';
            }
            legend.innerHTML = html;
        }

        function getFileTypes(node, result) {
            result = result || {};
            if (node.type === 'file' && node.extension) {
                result[node.extension] = (result[node.extension] || 0) + 1;
            }
            if (node.children) {
                node.children.forEach(child => getFileTypes(child, result));
            }
            return result;
        }

        renderSunburst(treeData);
    </script>
</body>
</html>"""

    html_content = html_template.replace("JSON_DATA_PLACEHOLDER", json_data)

    output_path = Path(html_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)


def log_error(errors, path: str, error_type: str, message: str) -> None:
    errors.append({
        "path": path,
        "type": error_type,
        "message": message,
    })


def get_file_types(node: Dict, result: Optional[Dict] = None) -> Dict:
    if result is None:
        result = {}
    if node.get("type") == "file" and node.get("extension"):
        ext = node.get("extension")
        result[ext] = result.get(ext, 0) + 1
    for child in node.get("children", []):
        get_file_types(child, result)
    return result
