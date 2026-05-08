import os, re

files = [
    r'd:\synthetic_market\auth-client\src\components\SimulatorPage.jsx',
    r'd:\synthetic_market\auth-client\src\components\StrategyEditor.jsx'
]

for fp in files:
    with open(fp, 'r', encoding='utf-8') as f:
        c = f.read()
    
    if 'import StarBorder' not in c:
        lines = c.split('\n')
        last_imp = max((i for i, l in enumerate(lines) if l.startswith('import ')), default=-1)
        if last_imp != -1:
            lines.insert(last_imp + 1, 'import StarBorder from "./StarBorder";')
        c = '\n'.join(lines)
    
    c = re.sub(r'<button\b', r'<StarBorder as="button"', c)
    c = re.sub(r'</button>', r'</StarBorder>', c)
    
    with open(fp, 'w', encoding='utf-8') as f:
        f.write(c)

print('Done')
