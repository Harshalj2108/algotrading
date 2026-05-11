import re
import glob

def fix_file(filepath):
    with open(filepath, 'r+', encoding='utf-8') as f:
        content = f.read()

        if 'Dashboard.jsx' in filepath:
            # Fix exhaustive-deps
            # We add display to the dependency array. It shouldn't cause infinite loop if used correctly,
            # but since we already tried replacing it, maybe it didn't match.
            # Let's just find the dependency array for that hook.
            content = re.sub(r'\}, \[value, duration\]\);', r'}, [value, duration, display]);', content)

        if 'SimChart.jsx' in filepath:
            # Fix empty blocks and unused vars
            content = re.sub(r'const\s+\[activeOsc,\s*setActiveOsc\]\s*=\s*useState\([^)]*\);?', '', content)
            content = re.sub(r'catch\s*\(\s*_[e]?\s*\)\s*\{\s*\}', r'catch { /* no-op */ }', content)
            content = re.sub(r'catch\s*\(\s*_[e]?\s*\)\s*\{', r'catch {', content)
            content = content.replace('catch { }', 'catch { /* no-op */ }')
            content = content.replace('catch {}', 'catch { /* no-op */ }')

        if 'SimulatorPage.jsx' in filepath:
            # Fix unused variables
            content = re.sub(r'const\s+\[ohlc,\s*setOhlc\]\s*=\s*useState\([^)]*\);?', '', content)
            content = re.sub(r'const\s+\[ebbBadge,\s*setEbbBadge\]\s*=\s*useState\([^)]*\);?', '', content)
            content = re.sub(r'const\s+p\s*=\s*msg\.price;', '', content)
            content = re.sub(r'setOhlc\(\{o:"—",h:"—",l:"—",c:"—"\}\);?', '', content)

        f.seek(0)
        f.write(content)
        f.truncate()

for filepath in glob.glob('auth-client/src/components/*.jsx'):
    fix_file(filepath)
print("Done fixing")
