import os

html_path = r"c:\Users\Shayara Basnet\OneDrive\Desktop\CAREGAP_1-main\CAREGAP_1-main\templates\dashboard.html"

with open(html_path, "r", encoding="utf-8") as f:
    lines = f.readlines()

css_lines = lines[12:1237]
js_lines = lines[1767:3067]

# Prepare static folders
base_dir = r"c:\Users\Shayara Basnet\OneDrive\Desktop\CAREGAP_1-main\CAREGAP_1-main"
static_css_dir = os.path.join(base_dir, "static", "css")
static_js_dir = os.path.join(base_dir, "static", "js")
os.makedirs(static_css_dir, exist_ok=True)
os.makedirs(static_js_dir, exist_ok=True)

with open(os.path.join(static_css_dir, "dashboard.css"), "w", encoding="utf-8") as f:
    f.writelines(css_lines)

with open(os.path.join(static_js_dir, "dashboard.js"), "w", encoding="utf-8") as f:
    f.writelines(js_lines)

# Reconstruct HTML
new_lines = []
for i, line in enumerate(lines):
    if i == 11: # where <style> starts
        new_lines.append(line) # keep <style> or wait, better to replace it with <link rel="...">
        # We will replace <style>...</style> with <link...>
        pass
    if i < 11:
        new_lines.append(line)
    elif i == 11:
        new_lines.append('  {% load static %}\n')
        new_lines.append('  <link rel="stylesheet" href="{% static \'css/dashboard.css\' %}">\n')
    elif 11 < i <= 1237:
        pass # skip css lines
    elif 1237 < i < 1766:
        new_lines.append(line)
    elif i == 1766:
        new_lines.append('  <script src="{% static \'js/dashboard.js\' %}"></script>\n')
    elif 1766 < i <= 3067:
        pass # skip js lines
    elif i > 3067:
        new_lines.append(line)

with open(html_path, "w", encoding="utf-8") as f:
    f.writelines(new_lines)

print("Done extracting and replacing.")
