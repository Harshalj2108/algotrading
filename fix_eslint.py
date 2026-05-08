import re, glob
for filepath in glob.glob('auth-client/src/**/*.{js,jsx}', recursive=True):
    with open(filepath, 'r+', encoding='utf-8') as f:
        content = f.read()
        
        if 'LandingPage.jsx' in filepath:
            content = content.replace("import React from 'react';\n", "")
            content = content.replace('import React from "react";\n', '')
        
        content = re.sub(r'catch\s*\(\s*err\s*\)\s*\{', r'catch {', content)
        content = re.sub(r'catch\s*\(\s*_[e]?\s*\)\s*\{', r'catch {', content)
        
        content = content.replace('catch { }', 'catch { /* no-op */ }')
        content = content.replace('catch {}', 'catch { /* no-op */ }')
        content = re.sub(r'catch\s*\{\s*\}', r'catch { /* no-op */ }', content)
        
        if 'SimChart.jsx' in filepath:
            content = re.sub(r'const\s+\[activeOsc,\s*setActiveOsc\]\s*=\s*useState\([^)]*\);?', '', content)

        if 'SimulatorPage.jsx' in filepath:
            content = re.sub(r'const\s+\[ohlc,\s*setOhlc\]\s*=\s*useState\([^)]*\);?', '', content)
            content = re.sub(r'const\s+\[ebbBadge,\s*setEbbBadge\]\s*=\s*useState\([^)]*\);?', '', content)
            content = re.sub(r'const\s+p\s*=\s*msg\.price;', '', content)
            content = re.sub(r'\}, \[\]\); // Add deps later.*', '}, [price, showToastMsg]);', content)
            content = content.replace('}, []); // Needs price, showToastMsg', '}, [price, showToastMsg]);')
            # I can just replace empty deps array with correct deps based on line numbers or just guess it
            content = content.replace('}, [fetchUserData]);', '}, [fetchUserData]);')

        if 'Dashboard.jsx' in filepath:
            content = content.replace('}, []); // Should be [display]', '}, [display]);')

        if 'StrategyEditor.jsx' in filepath:
            content = content.replace('}, [apiBase]);', '}, [apiBase, code]);')

        f.seek(0)
        f.write(content)
        f.truncate()
print('Done')