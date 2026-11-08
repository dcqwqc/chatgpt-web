"""Build the one offline acceptance response from real native DIL fixtures."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from web_content import render_web_elements
root = Path(__file__).resolve().parent
cases=json.loads((root/'native-chart-cases.json').read_text())
parts=['## Offline literal rendering acceptance\n\nFixed local data, not a model response or native ChatGPT screenshot.']
for case in cases[:3]:
    parts.append('<Chart content={'+json.dumps(case['spec'])+'} />')
parts.append('<AsyncImageGroup caption="Existing real BB captures"><AsyncImage src="/api/v1/plugins/image-generation/http/cli-image?asset=file_BBParityMetric20261108A" alt="Metric capture" caption="Actual metric screenshot"/><AsyncImage src="/api/v1/plugins/image-generation/http/cli-image?asset=file_BBParityBadge20261108A" alt="Badge capture" caption="Actual badge screenshot"/><AsyncImage query="Missing asset — unresolved"/></AsyncImageGroup>')
parts.extend(['<button>Save in ChatGPT Web</button>','<checkbox checked label="Literal checked state"/>','<checkbox checked={false} label="Literal unchecked state"/>'])
(root/'host-plugin/message.json').write_text(json.dumps({'text':render_web_elements('\n\n'.join(parts),rich_ui=True)},ensure_ascii=False,indent=2)+'\n')
