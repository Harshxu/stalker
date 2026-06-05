"""
Convert STALKER_SYSTEM_DOCUMENTATION.md to a styled PDF.
Uses fpdf2 for PDF generation with custom styling.
"""
import re
import os
from fpdf import FPDF

class StyledPDF(FPDF):
    def __init__(self):
        super().__init__()
        self.set_auto_page_break(auto=True, margin=20)
        
    def header(self):
        if self.page_no() > 1:
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(130, 130, 130)
            self.cell(0, 8, "STALKER - System Documentation v3.0", align="C")
            self.ln(5)
            self.set_draw_color(200, 200, 200)
            self.line(10, self.get_y(), 200, self.get_y())
            self.ln(5)
    
    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    def add_title_page(self):
        self.add_page()
        self.ln(60)
        self.set_font("Helvetica", "B", 32)
        self.set_text_color(20, 20, 80)
        self.cell(0, 15, "STALKER", align="C")
        self.ln(18)
        self.set_font("Helvetica", "", 16)
        self.set_text_color(80, 80, 80)
        self.cell(0, 10, "Stock Market Analyzer & Alpha Engine v3.0", align="C")
        self.ln(20)
        self.set_draw_color(20, 20, 80)
        self.set_line_width(0.8)
        self.line(60, self.get_y(), 150, self.get_y())
        self.ln(20)
        self.set_font("Helvetica", "", 12)
        self.set_text_color(100, 100, 100)
        self.cell(0, 8, "Complete System Documentation", align="C")
        self.ln(8)
        self.cell(0, 8, "Data Analysis Pipeline | Scoring Models | Risk Management", align="C")
        self.ln(30)
        self.set_font("Helvetica", "I", 10)
        self.set_text_color(150, 150, 150)
        self.cell(0, 8, "Last Updated: June 3, 2026", align="C")

    def section_heading(self, text, level=1):
        self.ln(4)
        text = self._safe(text)
        if level == 1:
            self.set_font("Helvetica", "B", 18)
            self.set_text_color(20, 20, 80)
            self.multi_cell(0, 10, text)
            self.set_draw_color(20, 20, 80)
            self.set_line_width(0.6)
            self.line(10, self.get_y() + 1, 200, self.get_y() + 1)
            self.ln(6)
        elif level == 2:
            self.set_font("Helvetica", "B", 14)
            self.set_text_color(40, 40, 100)
            self.multi_cell(0, 9, text)
            self.ln(3)
        elif level == 3:
            self.set_font("Helvetica", "B", 12)
            self.set_text_color(60, 60, 120)
            self.multi_cell(0, 8, text)
            self.ln(2)

    def body_text(self, text):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(40, 40, 40)
        self.multi_cell(0, 6, self._safe(text))
        self.ln(2)

    def bold_text(self, text):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(40, 40, 40)
        self.multi_cell(0, 6, self._safe(text))
        self.ln(2)

    def code_block(self, text):
        self.set_fill_color(240, 240, 245)
        self.set_font("Courier", "", 9)
        self.set_text_color(30, 30, 30)
        x = self.get_x()
        y = self.get_y()
        # Calculate height needed
        lines = text.strip().split("\n")
        block_height = len(lines) * 5.5 + 6
        # Check page break
        if y + block_height > 275:
            self.add_page()
            y = self.get_y()
        self.rect(10, y, 190, block_height, "F")
        self.set_xy(13, y + 3)
        for line in lines:
            safe_line = StyledPDF._safe(line)
            self.cell(0, 5.5, safe_line[:110])
            self.ln(5.5)
        self.ln(4)

    def add_table(self, headers, rows):
        # Calculate column widths
        n_cols = len(headers)
        available = 190
        col_widths = [available / n_cols] * n_cols
        
        # Adjust for content
        if n_cols >= 4:
            # Make first column narrower, description wider
            total = sum(col_widths)
            if n_cols == 4:
                col_widths = [15, 55, 45, 75]
            elif n_cols == 5:
                col_widths = [12, 40, 30, 35, 73]
            elif n_cols == 6:
                col_widths = [12, 35, 25, 30, 40, 48]
            else:
                col_widths = [available / n_cols] * n_cols
        elif n_cols == 3:
            col_widths = [50, 50, 90]
        elif n_cols == 2:
            col_widths = [70, 120]
        
        # Ensure widths sum to 190
        scale = 190 / sum(col_widths)
        col_widths = [w * scale for w in col_widths]
        
        # Header row
        self.set_font("Helvetica", "B", 9)
        self.set_fill_color(30, 30, 80)
        self.set_text_color(255, 255, 255)
        for i, h in enumerate(headers):
            safe_h = StyledPDF._safe(h)
            self.cell(col_widths[i], 8, safe_h[:30], border=1, fill=True, align="C")
        self.ln()
        
        # Data rows
        self.set_font("Helvetica", "", 8.5)
        fill = False
        for row in rows:
            self.set_fill_color(245, 245, 250) if fill else self.set_fill_color(255, 255, 255)
            self.set_text_color(40, 40, 40)
            max_h = 7
            for i, cell_text in enumerate(row):
                w = col_widths[i] if i < len(col_widths) else col_widths[-1]
                safe_text = StyledPDF._safe(str(cell_text))
                self.cell(w, max_h, safe_text[:50], border=1, fill=fill, align="L")
            self.ln()
            fill = not fill
        self.ln(4)

    def bullet_point(self, text, indent=0):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(40, 40, 40)
        x = 15 + indent * 5
        self.set_x(x)
        safe_text = self._safe(text)
        self.cell(5, 6, "-")
        self.multi_cell(185 - indent * 5, 6, safe_text)
        self.ln(1)

    @staticmethod
    def _safe(text):
        """Make text safe for latin-1 encoding (Helvetica built-in font)."""
        replacements = {
            "\u2014": "-", "\u2013": "-", "\u2192": "->", "\u2190": "<-",
            "\u2713": "[v]", "\u2717": "[x]", "\u2265": ">=", "\u2264": "<=",
            "\u2022": "-", "\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"',
            "\u2026": "...", "\u00d7": "x", "\u2248": "~", "\u2260": "!=",
            "\u20b9": "Rs.", "\u2103": "C",
        }
        for k, v in replacements.items():
            text = text.replace(k, v)
        # Final pass: strip any remaining non-latin1 characters
        return text.encode('latin-1', 'replace').decode('latin-1')


def sanitize(text):
    """Remove markdown formatting and emoji for safe PDF rendering."""
    # Remove bold/italic markers
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    # Remove markdown links
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
    # Remove emojis (common ones used in the doc)
    emoji_map = {
        "\u2764": "", "\U0001f4c8": "", "\U0001f4ca": "", "\u26a1": "", "\U0001f3ed": "",
        "\U0001f6e1": "", "\U0001f4e5": "", "\U0001f30e": "", "\U0001f4bc": "",
        "\u2705": "[OK]", "\u274c": "[X]", "\U0001f6a8": "[!]", "\U0001f7e1": "",
        "\U0001f680": "", "\u2753": "", "\u2197": "",
    }
    for emoji, replacement in emoji_map.items():
        text = text.replace(emoji, replacement)
    # Remove any remaining emoji-like unicode
    text = text.encode('ascii', 'replace').decode('ascii')
    return text.strip()


def parse_and_generate():
    md_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "STALKER_SYSTEM_DOCUMENTATION.md")
    pdf_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "STALKER_SYSTEM_DOCUMENTATION.pdf")
    
    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    pdf = StyledPDF()
    pdf.alias_nb_pages()
    pdf.add_title_page()
    
    lines = content.split("\n")
    i = 0
    in_code_block = False
    code_buffer = []
    in_table = False
    table_headers = []
    table_rows = []
    
    while i < len(lines):
        line = lines[i]
        
        # Code blocks
        if line.strip().startswith("```"):
            if in_code_block:
                # End code block
                pdf.code_block("\n".join(code_buffer))
                code_buffer = []
                in_code_block = False
            else:
                # Flush any pending table
                if in_table and table_headers:
                    pdf.add_table(table_headers, table_rows)
                    table_headers = []
                    table_rows = []
                    in_table = False
                in_code_block = True
            i += 1
            continue
        
        if in_code_block:
            code_buffer.append(sanitize(line))
            i += 1
            continue
        
        # Table detection
        if "|" in line and line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().split("|")[1:-1]]
            if all(set(c) <= set("- :") for c in cells):
                # Separator row, skip
                i += 1
                continue
            if not in_table:
                in_table = True
                table_headers = [sanitize(c) for c in cells]
            else:
                table_rows.append([sanitize(c) for c in cells])
            i += 1
            continue
        else:
            # Flush table if we were in one
            if in_table and table_headers:
                pdf.add_table(table_headers, table_rows)
                table_headers = []
                table_rows = []
                in_table = False
        
        stripped = line.strip()
        
        # Skip title page elements (already handled)
        if stripped.startswith("# STALKER") and "Documentation" in stripped:
            i += 1
            continue
        if stripped.startswith("### Stock Market"):
            i += 1
            continue
            
        # Blockquotes
        if stripped.startswith("> "):
            text = sanitize(stripped[2:])
            if text:
                pdf.set_font("Helvetica", "I", 10)
                pdf.set_text_color(80, 80, 120)
                pdf.set_fill_color(240, 240, 250)
                y = pdf.get_y()
                pdf.rect(10, y, 190, 10, "F")
                pdf.set_x(15)
                pdf.multi_cell(180, 6, StyledPDF._safe(text))
                pdf.ln(3)
            i += 1
            continue
        
        # Horizontal rules
        if stripped == "---":
            pdf.ln(3)
            pdf.set_draw_color(200, 200, 200)
            pdf.set_line_width(0.3)
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(5)
            i += 1
            continue
        
        # Headings
        if stripped.startswith("## "):
            heading_text = sanitize(stripped[3:])
            if heading_text:
                # Check if we need a new page for major sections
                if pdf.get_y() > 230:
                    pdf.add_page()
                pdf.section_heading(heading_text, level=1)
            i += 1
            continue
        
        if stripped.startswith("### "):
            heading_text = sanitize(stripped[4:])
            if heading_text:
                if pdf.get_y() > 250:
                    pdf.add_page()
                pdf.section_heading(heading_text, level=2)
            i += 1
            continue
        
        if stripped.startswith("#### "):
            heading_text = sanitize(stripped[5:])
            if heading_text:
                pdf.section_heading(heading_text, level=3)
            i += 1
            continue
        
        # Bullet points
        if stripped.startswith("- ") or stripped.startswith("* "):
            text = sanitize(stripped[2:])
            if text:
                pdf.bullet_point(text)
            i += 1
            continue
        
        # Numbered list items
        num_match = re.match(r'^(\d+)\.\s+(.+)', stripped)
        if num_match:
            text = sanitize(num_match.group(2))
            if text:
                pdf.set_font("Helvetica", "", 10)
                pdf.set_text_color(40, 40, 40)
                pdf.set_x(15)
                pdf.cell(8, 6, f"{num_match.group(1)}.")
                pdf.multi_cell(172, 6, StyledPDF._safe(text))
                pdf.ln(1)
            i += 1
            continue
        
        # Bold standalone lines
        if stripped.startswith("**") and stripped.endswith("**"):
            text = sanitize(stripped)
            if text:
                pdf.bold_text(text)
            i += 1
            continue
        
        # Regular paragraphs
        if stripped:
            text = sanitize(stripped)
            if text:
                pdf.body_text(text)
        elif not stripped:
            # Empty line
            pass
        
        i += 1
    
    # Flush any remaining table
    if in_table and table_headers:
        pdf.add_table(table_headers, table_rows)
    
    pdf.output(pdf_path)
    print(f"PDF generated: {pdf_path}")
    return pdf_path


if __name__ == "__main__":
    parse_and_generate()
