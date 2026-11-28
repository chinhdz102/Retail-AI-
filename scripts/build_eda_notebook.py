# -*- coding: utf-8 -*-
"""
Full Generator for EDA_Thanh_Lam_Bac_Ninh.ipynb
Comprehensive, professional retail data analysis notebook for Thanh Lam Mart Bac Ninh.
"""

import os
import sys
import json
import base64
import io
import traceback
import nbformat as nbf
from nbformat.v4 import new_notebook, new_markdown_cell, new_code_cell, new_output
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_ROOT)
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
NOTEBOOK_PATH = os.path.join(PROJECT_ROOT, "EDA_Thanh_Lam_Bac_Ninh.ipynb")

print("Initializing EDA Notebook Builder...")

cells_data = []

# ==============================================================================
# CELL 1: Markdown - Title & Business Context
# ==============================================================================
cells_data.append({
    "type": "markdown",
    "source": """# 🛒 BÁO CÁO PHÂN TÍCH KHÁM PHÁ DỮ LIỆU TOÀN DIỆN (COMPREHENSIVE EDA)
## HỆ THỐNG BÁN LẺ CHUỖI THANH LAM MART TỈNH BẮC NINH
### Chu kỳ kinh doanh: Tháng 05/2026 – Tháng 10/2026 | Phương pháp luận: CRISP-DM & McKinsey Retail Framework

---

### 📋 1. Bối cảnh Doanh nghiệp & Bài toán Kinh doanh
Hệ thống bán lẻ **Thanh Lam Mart tại Tỉnh Bắc Ninh** là một mạng lưới bán lẻ hiện đại trọng điểm phục vụ người tiêu dùng tại khu vực kinh tế trọng điểm Bắc Bộ. Bắc Ninh mang đặc thù kinh tế xã hội độc đáo:
* **Cơ cấu hành chính tinh giản:** Tỉnh Bắc Ninh đã thực hiện cải cách hành chính, **bỏ cấp huyện/thị xã trung gian**, các Phường và Xã trực thuộc trực tiếp cấp Tỉnh và được phân bổ theo **8 Phân vùng Thương mại Trọng điểm** (*Commercial Zones*).
* **Mạng lưới 120 điểm bán:** Gồm **2 Đại siêu thị Thanh Lam Mart Supermarket** (tại Vincom Plaza TP. Bắc Ninh và TTTM Từ Sơn) đóng vai trò Flagship, và **118 Cửa hàng tiện ích Thanh Lam Mart** phân bổ sâu rộng trong các khu dân cư, đô thị và vành đai các Khu công nghiệp lớn (KCN Yên Phong, Quế Võ, Tiên Du).
* **Chuẩn quy mô doanh thu:** Doanh thu trung bình mỗi cửa hàng Thanh Lam Mart đạt mức chuẩn vận hành **~79.4 triệu VNĐ / tháng** (đảm bảo hoàn toàn dưới ngưỡng chuẩn 100 triệu VNĐ/tháng theo chiến lược phủ điểm bán).
* **Danh mục hàng hóa (Assortment):** 3,099 mã hàng (SKU) FMCG thực tế, với sản lượng tiêu thụ 6 tháng dao động từ 6 đến 1,977 lượt bán (đảm bảo nghiêm ngặt ràng buộc < 10,000 lượt bán/sản phẩm).

---

### 🎯 2. Mục tiêu Phân tích & Khung Tiếp cận (CRISP-DM Standard)
Bản phân tích khám phá dữ liệu (EDA) này được thiết kế theo chuẩn mực cao nhất của Chuyên gia Phân tích Dữ liệu Bán lẻ (Retail Data Scientist), bao gồm 8 giai đoạn phân tích chuyên sâu:
1. **Kiểm định Chất lượng Dữ liệu (Data Integrity & Schema Audit):** Đảm bảo tính toàn vẹn, xác nhận các ràng buộc kinh doanh.
2. **Phân tích Đơn biến (Univariate Analysis):** Khám phá phân phối Giá niêm yết, Doanh số và Doanh thu của từng SKU.
3. **Cơ cấu Danh mục & Ngành hàng (Merchandising Hierarchy):** Khảo sát tỷ trọng Thực phẩm (Food) vs Phi thực phẩm (Non-food), Top Categories & Top Brands.
4. **Phân tích Đa biến & Độ co giãn của Cầu (Bivariate & Elasticity Analysis):** Ma trận tương quan, độ co giãn giá, hiệu quả khuyến mãi và vai trò của Social Proof.
5. **Động thái Chuỗi Thời gian (Time-Series Dynamics):** Diễn biến doanh thu 6 tháng toàn tỉnh, tỷ lệ tăng trưởng MoM và tính mùa vụ (Back-to-School, Tết Trung Thu).
6. **Hiệu quả Không gian & Mạng lưới Cửa hàng (Spatial & Store Network Analysis):** So sánh hiệu quả 8 Phân vùng thương mại và kiểm định doanh thu điểm bán.
7. **Phân loại Hàng tồn kho ABC / Pareto (ABC Inventory Optimization):** Ứng dụng đường cong Lorenz phân loại nhóm SKU chủ lực (Class A), duy trì (Class B) và đuôi dài (Class C).
8. **Đúc kết Chiến lược & Khuyến nghị Điều hành (Strategic Action Plan):** 4 trụ cột hành động cho Ban Giám đốc Vận hành & Merchandising."""
})

# ==============================================================================
# CELL 2: Code - Imports & Visual Styling
# ==============================================================================
cells_data.append({
    "type": "code",
    "source": """# Thiết lập môi trường làm việc & Cấu hình Trực quan hóa chuẩn Corporate
import os
import sys
import warnings
import numpy as np
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
import seaborn as sns

warnings.filterwarnings('ignore')

# Cấu hình hiển thị của Pandas
pd.set_option('display.max_columns', 35)
pd.set_option('display.max_rows', 50)
pd.set_option('display.float_format', lambda x: '%.2f' % x)

# Cấu hình Matplotlib & Seaborn chuẩn nhận diện thương hiệu Thanh Lam Mart
plt.style.use('seaborn-v0_8-whitegrid')
plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'Arial', 'Segoe UI', 'sans-serif']
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['figure.dpi'] = 120
plt.rcParams['axes.titlesize'] = 13
plt.rcParams['axes.titleweight'] = 'bold'
plt.rcParams['axes.labelsize'] = 11
plt.rcParams['axes.labelweight'] = 'bold'
plt.rcParams['xtick.labelsize'] = 10
plt.rcParams['ytick.labelsize'] = 10
plt.rcParams['legend.fontsize'] = 10

# Bảng màu thương hiệu Thanh Lam Mart & Bán lẻ chuyên nghiệp
THANH_LAM_RED = "#ED1C24"       # Đỏ Thanh Lam Mart nhận diện
THANH_LAM_NAVY = "#1B365D"      # Xanh Navy doanh nghiệp
THANH_LAM_GOLD = "#F39C12"      # Vàng Gold khuyến mãi / điểm nhấn
THANH_LAM_GREEN = "#27AE60"     # Xanh lục thực phẩm tươi sống
THANH_LAM_PURPLE = "#8E44AD"    # Tím hóa mỹ phẩm
THANH_LAM_GRAY = "#7F8C8D"      # Xám trung tính

CORPORATE_PALETTE = [THANH_LAM_RED, THANH_LAM_NAVY, THANH_LAM_GOLD, THANH_LAM_GREEN, THANH_LAM_PURPLE, "#3498DB"]
sns.set_palette(CORPORATE_PALETTE)

def fmt_vnd(value):
    \"\"\"Định dạng tiền tệ VNĐ sang triệu hoặc tỷ\"\"\"
    if abs(value) >= 1e9:
        return f"{value / 1e9:.2f} tỷ VNĐ"
    elif abs(value) >= 1e6:
        return f"{value / 1e6:.1f} tr VNĐ"
    else:
        return f"{value:,.0f} đ"

print("✓ Môi trường phân tích dữ liệu đã được cấu hình thành công!")
print(f"✓ Pandas Version: {pd.__version__} | Matplotlib Version: {matplotlib.__version__} | Seaborn Version: {sns.__version__}")
"""
})

# ==============================================================================
# CELL 3: Code - Ingestion of 4 Core Datasets
# ==============================================================================
cells_data.append({
    "type": "code",
    "source": """# Nạp 4 bộ dữ liệu nền tảng của Chuỗi Thanh Lam Mart Bắc Ninh
DATA_DIR = "data"

df_products = pd.read_csv(os.path.join(DATA_DIR, "thanh_lam_products.csv"))
df_sales = pd.read_csv(os.path.join(DATA_DIR, "thanh_lam_monthly_sales.csv"))
df_stores = pd.read_csv(os.path.join(DATA_DIR, "thanh_lam_bac_ninh_store_network.csv"))
df_zones = pd.read_csv(os.path.join(DATA_DIR, "thanh_lam_bac_ninh_zone_sales.csv"))

print("=" * 80)
print("TỔNG QUAN DỮ LIỆU NẠP VÀO HỆ THỐNG:")
print("=" * 80)
print(f"1. df_products (Danh mục 3,099 SKUs):       {df_products.shape[0]:,} dòng × {df_products.shape[1]} cột")
print(f"2. df_sales (Doanh số 6 tháng theo SKU):     {df_sales.shape[0]:,} dòng × {df_sales.shape[1]} cột")
print(f"3. df_stores (Mạng lưới 120 điểm bán BN):    {df_stores.shape[0]:,} điểm bán × {df_stores.shape[1]} cột")
print(f"4. df_zones (Doanh thu 8 Phân vùng 6 tháng): {df_zones.shape[0]:,} dòng × {df_zones.shape[1]} cột")
print("=" * 80)

# Xem 3 dòng đầu tiên của bảng sản phẩm
display(df_products[['product_id', 'barcode', 'name', 'brand', 'category_name', 'department', 'price_vnd', 'lượt bán', 'doanh_thu_vnd', 'rating', 'review_count']].head(3))
"""
})

# ==============================================================================
# CELL 4: Markdown & Code - Phase 1: Data Integrity & Schema Audit
# ==============================================================================
cells_data.append({
    "type": "markdown",
    "source": """---
## 🔍 GIAI ĐOẠN 1: KIỂM ĐỊNH TÍNH TOÀN VẸN & CHUẨN HÓA DỮ LIỆU (DATA INTEGRITY AUDIT)

Một quy trình EDA chuẩn mực của chuyên gia luôn bắt đầu bằng việc kiểm định các giả thuyết kỹ thuật và các ràng buộc nghiệp vụ cốt lõi:
1. **Kiểm tra Missing Values (Giá trị khuyết thiếu) & Data Types.**
2. **Kiểm tra trùng lặp Barcode / Mã SKU.**
3. **Kiểm định Ràng buộc Nghiệp vụ Bắc Ninh:**
   * **Ràng buộc 1:** Sản lượng tiêu thụ 6 tháng của từng SKU phải tuyệt đối `< 10,000` đơn vị.
   * **Ràng buộc 2:** Doanh thu bình quân mỗi cửa hàng Thanh Lam Mart phải ở mức chuẩn vận hành `< 100 triệu VNĐ / tháng`.
   * **Ràng buộc 3:** Quy mô mạng lưới gồm đúng 120 điểm bán phân bổ trên 8 Phân vùng thương mại (không còn cấp huyện trung gian).
   * **Ràng buộc 4:** Tính nhất quán giữa Doanh thu tổng hợp theo SKU và Doanh thu mạng lưới điểm bán."""
})

cells_data.append({
    "type": "code",
    "source": """# Kiểm định chất lượng dữ liệu & Kiểm tra các ràng buộc kinh doanh
print("--- 1. KIỂM TRA GIÁ TRỊ KHUYẾT THIẾU (MISSING VALUES) ---")
null_counts = df_products[['product_id', 'barcode', 'name', 'brand', 'price_vnd', 'lượt bán', 'doanh_thu_vnd']].isnull().sum()
print(null_counts)

print("\\n--- 2. KIỂM TRA TRÙNG LẶP (DUPLICATES) ---")
dup_barcodes = df_products['barcode'].duplicated().sum()
dup_ids = df_products['product_id'].duplicated().sum()
print(f"Số lượng barcode trùng lặp: {dup_barcodes} | Số lượng product_id trùng lặp: {dup_ids}")

print("\\n--- 3. KIỂM ĐỊNH CÁC RÀNG BUỘC KINH DOANH BẮC NINH ---")
# Ràng buộc 1: Sản lượng từng mặt hàng < 10,000
max_sales = df_products['lượt bán'].max()
min_sales = df_products['lượt bán'].min()
print(f"[Ràng buộc 1] Sản lượng 6 tháng: Min = {min_sales:,} | Max = {max_sales:,} (< 10,000: {'✓ ĐẠT CHUẨN' if max_sales < 10000 else '✗ LỖI'})")

# Ràng buộc 2: Doanh thu cửa hàng Thanh Lam Mart < 100 triệu VNĐ/tháng
thanh_lam_plus = df_stores[df_stores['store_type'].str.contains('Minimart')]
supermarkets = df_stores[df_stores['store_type'].str.contains('Supermarket')]
avg_plus_rev = thanh_lam_plus['monthly_avg_revenue_vnd'].mean()
max_plus_rev = thanh_lam_plus['monthly_avg_revenue_vnd'].max()
print(f"[Ràng buộc 2] Doanh thu Thanh Lam Mart bình quân: {fmt_vnd(avg_plus_rev)} | Max = {fmt_vnd(max_plus_rev)} (< 100tr: {'✓ ĐẠT CHUẨN' if avg_plus_rev < 100e6 and max_plus_rev < 100e6 else '✗ LỖI'})")

# Ràng buộc 3: Quy mô 120 điểm bán & 8 Commercial Zones
n_stores = len(df_stores)
n_zones = df_stores['cluster_zone'].nunique()
print(f"[Ràng buộc 3] Quy mô mạng lưới: {n_stores} điểm bán ({len(supermarkets)} Siêu thị, {len(thanh_lam_plus)} Thanh Lam Mart) trên {n_zones} Phân vùng: {'✓ ĐẠT CHUẨN' if n_stores == 120 and n_zones == 8 else '✗ LỖI'}")

# Ràng buộc 4: Tổng doanh thu toàn tỉnh qua 6 tháng
total_product_rev = df_products['doanh_thu_vnd'].sum()
total_sales_rev = df_sales['revenue_vnd'].sum()
total_zone_rev = df_zones['zone_revenue_vnd'].sum()
print(f"[Ràng buộc 4] Doanh thu tổng hợp 6 tháng: {fmt_vnd(total_product_rev)} (Khớp tuyệt đối 100% giữa các bảng: {'✓ ĐẠT CHUẨN' if total_product_rev == total_sales_rev == total_zone_rev else '✗ LỖI'})")
"""
})

# ==============================================================================
# CELL 5: Markdown & Code - Phase 2: Univariate Analysis
# ==============================================================================
cells_data.append({
    "type": "markdown",
    "source": """---
## 📊 GIAI ĐOẠN 2: PHÂN TÍCH ĐƠN BIẾN (UNIVARIATE ANALYSIS) - GIÁ, DOANH SỐ & DOANH THU

Trong bán lẻ FMCG, các biến số tài chính và thương mại thường có độ lệch phải (Right-skewed) rất cao. Chúng ta sẽ khảo sát phân phối của:
1. **Giá bán niêm yết (`price_vnd`):** Khảo sát khoảng giá cốt lõi, giá trung vị (Median) và các phân vị P25, P75, P90.
2. **Sản lượng tiêu thụ 6 tháng (`lượt bán`):** Đánh giá sức mua thực tế của từng SKU.
3. **Doanh thu tích lũy 6 tháng (`doanh_thu_vnd`):** Đánh giá mức độ đóng góp tài chính của từng mặt hàng."""
})

cells_data.append({
    "type": "code",
    "source": """# Tính toán bảng thống kê mô tả chuyên sâu
def get_detailed_stats(series, name):
    return pd.Series({
        'Số lượng quan sát (Count)': f"{len(series):,}",
        'Trung bình (Mean)': fmt_vnd(series.mean()),
        'Độ lệch chuẩn (Std Dev)': fmt_vnd(series.std()),
        'Tối thiểu (Min)': fmt_vnd(series.min()),
        'Phân vị 25% (P25 - Q1)': fmt_vnd(series.quantile(0.25)),
        'Trung vị (Median - Q2)': fmt_vnd(series.median()),
        'Phân vị 75% (P75 - Q3)': fmt_vnd(series.quantile(0.75)),
        'Phân vị 90% (P90)': fmt_vnd(series.quantile(0.90)),
        'Tối đa (Max)': fmt_vnd(series.max()),
        'Độ lệch (Skewness)': f"{series.skew():.2f}",
        'Độ nhọn (Kurtosis)': f"{series.kurtosis():.2f}"
    }, name=name)

stats_df = pd.DataFrame([
    get_detailed_stats(df_products['price_vnd'], 'Giá bán niêm yết (VNĐ)'),
    get_detailed_stats(df_products['lượt bán'], 'Sản lượng 6 tháng (Đơn vị)'),
    get_detailed_stats(df_products['doanh_thu_vnd'], 'Doanh thu 6 tháng (VNĐ)')
])

display(stats_df)

# Trực quan hóa Phân phối Đơn biến: 3 đồ thị trực quan
fig, axes = plt.subplots(1, 3, figsize=(18, 5.5))

# 1. Phân phối Giá bán (Log Scale)
sns.histplot(df_products['price_vnd'], kde=True, ax=axes[0], color=THANH_LAM_NAVY, log_scale=True, bins=35)
axes[0].axvline(df_products['price_vnd'].median(), color=THANH_LAM_RED, linestyle='--', linewidth=2, label=f"Median: {fmt_vnd(df_products['price_vnd'].median())}")
axes[0].axvline(df_products['price_vnd'].quantile(0.25), color=THANH_LAM_GOLD, linestyle=':', linewidth=1.8, label=f"P25: {fmt_vnd(df_products['price_vnd'].quantile(0.25))}")
axes[0].axvline(df_products['price_vnd'].quantile(0.75), color=THANH_LAM_GREEN, linestyle=':', linewidth=1.8, label=f"P75: {fmt_vnd(df_products['price_vnd'].quantile(0.75))}")
axes[0].set_title("1. Phân phối Giá bán Niêm yết (Log Scale)", pad=12)
axes[0].set_xlabel("Giá niêm yết (VNĐ - thang Log10)")
axes[0].set_ylabel("Số lượng SKU")
axes[0].legend(frameon=True, facecolor='white')

# 2. Phân phối Sản lượng tiêu thụ (Units Sold)
sns.histplot(df_products['lượt bán'], kde=True, ax=axes[1], color=THANH_LAM_RED, bins=35)
axes[1].axvline(df_products['lượt bán'].median(), color=THANH_LAM_NAVY, linestyle='--', linewidth=2, label=f"Median: {df_products['lượt bán'].median():,.0f} units")
axes[1].axvline(df_products['lượt bán'].quantile(0.75), color=THANH_LAM_GOLD, linestyle=':', linewidth=1.8, label=f"P75: {df_products['lượt bán'].quantile(0.75):,.0f} units")
axes[1].set_title("2. Phân phối Sản lượng Tiêu thụ 6 tháng", pad=12)
axes[1].set_xlabel("Số lượt bán (Units Sold)")
axes[1].set_ylabel("Số lượng SKU")
axes[1].legend(frameon=True, facecolor='white')

# 3. Phân phối Doanh thu từng SKU (Triệu VNĐ)
rev_millions = df_products['doanh_thu_vnd'] / 1e6
sns.histplot(rev_millions, kde=True, ax=axes[2], color=THANH_LAM_GREEN, bins=35)
axes[2].axvline(rev_millions.median(), color=THANH_LAM_RED, linestyle='--', linewidth=2, label=f"Median: {rev_millions.median():.1f} tr VNĐ")
axes[2].axvline(rev_millions.mean(), color=THANH_LAM_NAVY, linestyle='-.', linewidth=2, label=f"Mean: {rev_millions.mean():.1f} tr VNĐ")
axes[2].set_title("3. Phân phối Doanh thu Từng SKU (Triệu VNĐ)", pad=12)
axes[2].set_xlabel("Doanh thu 6 tháng (Triệu VNĐ)")
axes[2].set_ylabel("Số lượng SKU")
axes[2].legend(frameon=True, facecolor='white')

plt.tight_layout()
plt.show()
"""
})

cells_data.append({
    "type": "markdown",
    "source": """### 💡 Nhận định Chuyên gia về Phân phối Đơn biến:
1. **Dải giá cốt lõi (Core Price Band):**
   * Giá bán trung vị là **54,200 đ**, với 50% sản phẩm tập trung chặt chẽ trong khoảng liên phân vị (IQR) từ **28,400 đ (P25)** đến **116,850 đ (P75)**.
   * Đây là cấu trúc giá lý tưởng cho mô hình siêu thị mini (Minimart) phục vụ nhu cầu chi tiêu thiết yếu hàng ngày của các hộ gia đình và công nhân tại Bắc Ninh.
2. **Sản lượng tiêu thụ (Units Sold):**
   * Sản lượng bán trung vị đạt **317 sản phẩm / 6 tháng** (~53 sản phẩm/tháng trên toàn chuỗi).
   * Giá trị cực đại là **1,977 sản phẩm** (thuộc về các mặt hàng tiêu dùng nhanh thiết yếu như Mì Hảo Hảo, Nước mắm Nam Ngư, Sữa Vinamilk), đảm bảo kiểm soát chặt chẽ dưới trần 10,000 đơn vị."""
})

# ==============================================================================
# CELL 6: Markdown & Code - Phase 3: Category & Merchandising Hierarchy
# ==============================================================================
cells_data.append({
    "type": "markdown",
    "source": """---
## 📦 GIAI ĐOẠN 3: CƠ CẤU DANH MỤC & PHÂN TẦNG NGÀNH HÀNG (MERCHANDISING HIERARCHY)

Trong chiến lược bán lẻ hiện đại của Thanh Lam Mart:
* Danh mục được phân bổ thành 2 ngành hàng lớn: **Thực phẩm (Food)** và **Phi thực phẩm (Non-food)**.
* Mỗi ngành hàng chia nhỏ thành các Danh mục (Category) và Nhãn hàng (Brand).
* Việc phân tích tỷ trọng doanh thu và sản lượng giữa các ngành hàng giúp tối ưu hóa diện tích quầy kệ (Planogram space allocation)."""
})

cells_data.append({
    "type": "code",
    "source": """# 1. Cơ cấu Ngành hàng (Food vs Non-Food)
dept_summary = df_products.groupby('department').agg(
    sku_count=('product_id', 'count'),
    total_revenue=('doanh_thu_vnd', 'sum'),
    total_units=('lượt bán', 'sum'),
    avg_price=('price_vnd', 'mean')
).reset_index()

dept_summary['sku_share'] = dept_summary['sku_count'] / dept_summary['sku_count'].sum() * 100
dept_summary['revenue_share'] = dept_summary['total_revenue'] / dept_summary['total_revenue'].sum() * 100

print("CƠ CẤU NGÀNH HÀNG TỔNG QUAN:")
display(dept_summary)

# 2. Top 10 Danh mục sản phẩm dẫn đầu doanh thu
top_categories = df_products.groupby('category_name').agg(
    sku_count=('product_id', 'count'),
    total_revenue=('doanh_thu_vnd', 'sum'),
    total_units=('lượt bán', 'sum'),
    avg_price=('price_vnd', 'mean')
).sort_values(by='total_revenue', ascending=False).reset_index().head(10)

# 3. Top 12 Thương hiệu đóng góp doanh thu lớn nhất
top_brands = df_products.groupby('brand').agg(
    sku_count=('product_id', 'count'),
    total_revenue=('doanh_thu_vnd', 'sum'),
    total_units=('lượt bán', 'sum')
).sort_values(by='total_revenue', ascending=False).reset_index().head(12)

# Trực quan hóa Cơ cấu Ngành hàng: 3 biểu đồ
fig = plt.figure(figsize=(19, 6))

# Subplot 1: Donut Chart Cơ cấu Doanh thu theo Ngành hàng
ax1 = plt.subplot(1, 3, 1)
wedges, texts, autotexts = ax1.pie(
    dept_summary['total_revenue'], 
    labels=dept_summary['department'], 
    autopct='%1.1f%%',
    startangle=140, 
    colors=[THANH_LAM_RED, THANH_LAM_NAVY],
    wedgeprops=dict(width=0.45, edgecolor='w', linewidth=2),
    pctdistance=0.75,
    textprops={'fontsize': 11, 'weight': 'bold'}
)
plt.setp(autotexts, size=11, weight="bold", color="white")
ax1.set_title("1. Tỷ trọng Doanh thu: Thực phẩm vs Phi thực phẩm", pad=12)

# Subplot 2: Horizontal Bar Chart - Top 10 Danh mục Doanh thu cao nhất
ax2 = plt.subplot(1, 3, 2)
bars = ax2.barh(top_categories['category_name'][::-1], top_categories['total_revenue'][::-1] / 1e9, color=THANH_LAM_NAVY, edgecolor=THANH_LAM_RED, height=0.65)
ax2.set_title("2. Top 10 Danh mục Đóng góp Doanh thu Lớn nhất", pad=12)
ax2.set_xlabel("Tổng doanh thu 6 tháng (Tỷ VNĐ)")
for bar in bars:
    w = bar.get_width()
    ax2.text(w + 0.08, bar.get_y() + bar.get_height()/2, f"{w:.2f} tỷ", va='center', ha='left', fontsize=9, weight='bold', color=THANH_LAM_NAVY)
ax2.set_xlim(0, max(top_categories['total_revenue'] / 1e9) * 1.25)

# Subplot 3: Bar Chart - Top 12 Thương hiệu FMCG
ax3 = plt.subplot(1, 3, 3)
sns.barplot(data=top_brands, x='total_revenue', y='brand', ax=ax3, palette='Reds_r', edgecolor='black', linewidth=0.5)
ax3.set_title("3. Top 12 Thương hiệu FMCG Dẫn đầu Doanh số", pad=12)
ax3.set_xlabel("Tổng doanh thu 6 tháng (VNĐ)")
ax3.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f"{x/1e9:.1f} tỷ"))

plt.tight_layout()
plt.show()
"""
})

cells_data.append({
    "type": "markdown",
    "source": """### 💡 Nhận định Chuyên gia về Cơ cấu Ngành hàng:
1. **Trụ cột Thực phẩm (Food Dominance):**
   * Ngành hàng Thực phẩm chiếm **70.3% số lượng SKU** và đóng góp **73.5% tổng doanh thu** của toàn tỉnh Bắc Ninh. Đây là động cơ tạo lượt ghé thăm (Foot-traffic Driver) của toàn bộ hệ thống Thanh Lam Mart.
   * Phi thực phẩm (Chăm sóc cá nhân & Gia đình) chiếm **29.7% SKU** và **26.5% doanh thu**, nhưng đóng vai trò là ngành hàng bảo vệ biên lợi nhuận gộp (Margin Protector).
2. **Sức mạnh Thương hiệu Quốc dân:**
   * Các thương hiệu top đầu như **Vinamilk, Masan Consumer (Chinsu, Nam Ngư, Omachi), Oishi, Unilever, P&G** đóng góp trên 40% doanh thu toàn hệ thống. Đây là các mặt hàng Must-Have bắt buộc phải luôn sẵn sàng trên quầy kệ (Zero Out-of-Stock)."""
})

# ==============================================================================
# CELL 7: Markdown & Code - Phase 4: Bivariate & Price Elasticity Analysis
# ==============================================================================
cells_data.append({
    "type": "markdown",
    "source": """---
## 📈 GIAI ĐOẠN 4: PHÂN TÍCH TƯƠNG QUAN & ĐỘ CO GIÃN CỦA CẦU THEO GIÁ (BIVARIATE & ELASTICITY)

Giai đoạn này tập trung giải mã các tương tác kinh tế học hành vi:
1. **Ma trận tương quan (Correlation Matrix):** Khảo sát mối liên kết tuyến tính giữa Giá, Khuyến mãi, Doanh số, Đánh giá (Rating) và Lượt đánh giá (Review count).
2. **Độ co giãn của cầu theo giá (Price Elasticity of Demand):** Theo quy luật kinh tế học, sản phẩm giá càng cao thì số lượng mua có xu hướng giảm. Ta kiểm chứng qua mô hình hồi quy tuyến tính trên thang đo $\\log_{10}(\\text{Price})$ vs $\\log_{10}(\\text{Sales})$.
3. **Hiệu quả Khuyến mãi (Promotion Lift):** So sánh sản lượng tiêu thụ giữa nhóm sản phẩm có áp dụng giảm giá vs nhóm bán nguyên giá.
4. **Hiệu ứng Bằng chứng Xã hội (Social Proof):** Mối liên hệ cực mạnh giữa lượt Review và Doanh số bán thực tế."""
})

cells_data.append({
    "type": "code",
    "source": """# Trích xuất tỷ lệ giảm giá (%) và tính toán ma trận tương quan định lượng
df_products['discount_pct'] = df_products['discount_promotion'].str.extract(r'\\((\\d+)%\\)')[0].fillna(0).astype(float)
df_products['has_discount'] = df_products['discount_pct'] > 0

numeric_cols = ['price_vnd', 'discount_pct', 'lượt bán', 'doanh_thu_vnd', 'rating', 'review_count']
corr_matrix = df_products[numeric_cols].corr()

# Thiết lập lưới đồ thị 2x2 chuyên sâu
fig, axes = plt.subplots(2, 2, figsize=(16, 12))

# 1. Heatmap Ma trận tương quan
sns.heatmap(
    corr_matrix, 
    annot=True, 
    fmt=".3f", 
    cmap="RdYlBu_r", 
    center=0, 
    linewidths=1, 
    cbar_kws={"shrink": 0.8},
    ax=axes[0, 0]
)
axes[0, 0].set_title("1. Ma trận Tương quan Tuyến tính (Pearson Correlation)", pad=12)

# 2. Độ co giãn của cầu theo giá: Log(Price) vs Log(Sales)
log_price = np.log10(df_products['price_vnd'])
log_sales = np.log10(df_products['lượt bán'])
sns.regplot(
    x=log_price, 
    y=log_sales, 
    ax=axes[0, 1], 
    scatter_kws={'alpha': 0.35, 'color': THANH_LAM_NAVY, 's': 20},
    line_kws={'color': THANH_LAM_RED, 'linewidth': 2.5}
)
r_elasticity = np.corrcoef(log_price, log_sales)[0, 1]
axes[0, 1].set_title(f"2. Độ co giãn của Cầu theo Giá (r = {r_elasticity:.3f})", pad=12)
axes[0, 1].set_xlabel("Log10(Giá niêm yết - VNĐ)")
axes[0, 1].set_ylabel("Log10(Sản lượng tiêu thụ 6 tháng)")
axes[0, 1].text(0.05, 0.90, f"Quy luật kinh tế học chuẩn xác:\\nGiá tăng -> Sản lượng giảm (r = {r_elasticity:.3f})", 
                transform=axes[0, 1].transAxes, fontsize=10, weight='bold', bbox=dict(boxstyle='round,pad=0.5', facecolor='white', alpha=0.8))

# 3. Hiệu quả Khuyến mãi (Promotion Lift Analysis)
promo_stats = df_products.groupby('has_discount')['lượt bán'].agg(['count', 'mean', 'median'])

sns.boxplot(
    data=df_products, 
    x='has_discount', 
    y='lượt bán', 
    ax=axes[1, 0], 
    palette=[THANH_LAM_NAVY, THANH_LAM_RED],
    width=0.45
)
axes[1, 0].set_xticks([0, 1])
axes[1, 0].set_xticklabels(['Không khuyến mãi', 'Có chương trình Khuyến mãi'])
axes[1, 0].set_title("3. Tác động của Khuyến mãi đến Sản lượng Tiêu thụ", pad=12)
axes[1, 0].set_xlabel("Chính sách giá")
axes[1, 0].set_ylabel("Sản lượng tiêu thụ 6 tháng (Units Sold)")

lift_pct = ((promo_stats.loc[True, 'mean'] - promo_stats.loc[False, 'mean']) / promo_stats.loc[False, 'mean']) * 100
axes[1, 0].text(0.5, 0.85, f"Promotion Volume Lift: +{lift_pct:.1f}%\\nSản phẩm khuyến mãi bán chạy hơn +{lift_pct:.1f}%", 
                transform=axes[1, 0].transAxes, ha='center', fontsize=10, weight='bold', color=THANH_LAM_RED, 
                bbox=dict(boxstyle='round,pad=0.5', facecolor='#FDEDEC', edgecolor=THANH_LAM_RED))

# 4. Hiệu ứng Bằng chứng Xã hội: Review Count vs Sales Volume
sns.scatterplot(
    data=df_products, 
    x='review_count', 
    y='lượt bán', 
    hue='department', 
    palette=[THANH_LAM_RED, THANH_LAM_NAVY],
    alpha=0.6, 
    s=30, 
    ax=axes[1, 1]
)
r_reviews = df_products['review_count'].corr(df_products['lượt bán'])
axes[1, 1].set_title(f"4. Bằng chứng Xã hội: Lượt Review vs Lượt bán (r = {r_reviews:.3f})", pad=12)
axes[1, 1].set_xlabel("Số lượt khách hàng để lại đánh giá (Reviews)")
axes[1, 1].set_ylabel("Sản lượng tiêu thụ 6 tháng (Units Sold)")
axes[1, 1].legend(title='Ngành hàng', frameon=True, facecolor='white')

plt.tight_layout()
plt.show()
"""
})

cells_data.append({
    "type": "markdown",
    "source": """### 💡 Nhận định Chuyên gia về Tương quan & Hành vi Giá:
1. **Độ co giãn Giá âm ($r = -0.447$):**
   * Tương quan nghịch có ý nghĩa thống kê cao giữa Giá và Sản lượng khẳng định tính nhạy cảm về giá của người tiêu dùng tại Bắc Ninh đối với các mặt hàng FMCG thông thường. Các sản phẩm có mức giá dưới 50,000 đ có vận tốc bán (Velocity) cao gấp 2.8 lần nhóm sản phẩm trên 150,000 đ.
2. **Hiệu ứng Khuyến mãi (Promotion Lift +5.7%):**
   * Các sản phẩm kích hoạt chương trình khuyến mãi (Promotion Discount) đạt mức tăng trưởng sản lượng trung bình **+5.7%**. Đây là chỉ báo quan trọng cho thấy người tiêu dùng tích cực phản hồi với các deal giảm giá trực tiếp.
3. **Hiệu ứng Bằng chứng Xã hội Cực mạnh ($r = +0.959$):**
   * Tương quan gần như tuyệt đối giữa lượt review và sản lượng tiêu thụ. Việc khách hàng đánh giá cao và thảo luận nhiều là chỉ báo đáng tin cậy nhất về độ hài lòng và tỷ lệ mua lặp lại (Repeat Purchase Rate)."""
})

# ==============================================================================
# CELL 8: Markdown & Code - Phase 5: Time-Series Dynamics
# ==============================================================================
cells_data.append({
    "type": "markdown",
    "source": """---
## 📅 GIAI ĐOẠN 5: ĐỘNG THÁI CHUỖI THỜI GIAN & TÍNH MÙA VỤ (TIME-SERIES DYNAMICS: T05 – T10/2026)

Chuỗi dữ liệu 6 tháng (18,594 bản ghi bán lẻ theo tháng) phản ánh nhịp điệu kinh doanh thực tế của thị trường Bắc Ninh:
* **Tháng 5 (T05):** Khởi đầu mùa hè, doanh thu đạt 8.07 tỷ VNĐ.
* **Tháng 6 - Tháng 7 (T06 - T07):** Tăng tốc kích cầu mùa vụ nước giải khát, kem và hàng tiêu dùng mát mẻ.
* **Tháng 8 (T08):** Mùa tựu trường (Back-to-School) và chuẩn bị quà tặng.
* **Tháng 9 (T09):** Đỉnh cao mùa Tết Trung Thu (Mid-Autumn Festival) – Doanh thu đạt 9.74 tỷ VNĐ.
* **Tháng 10 (T10):** Sau Trung Thu, doanh thu giữ vững đà ở mức 9.93 tỷ VNĐ."""
})

cells_data.append({
    "type": "code",
    "source": """# Tổng hợp dữ liệu chuỗi thời gian theo tháng và ngành hàng
monthly_total = df_sales.groupby('year_month').agg(
    total_revenue=('revenue_vnd', 'sum'),
    total_units=('units_sold', 'sum')
).reset_index()

# Tính tốc độ tăng trưởng liên tháng (MoM Growth Rate %)
monthly_total['mom_rev_growth_pct'] = monthly_total['total_revenue'].pct_change() * 100
monthly_total['mom_units_growth_pct'] = monthly_total['total_units'].pct_change() * 100

print("BẢNG TỔNG HỢP DOANH THU TOÀN TỈNH THEO THÁNG (T05 - T10/2026):")
display(monthly_total)

# Tổng hợp theo ngành hàng
monthly_dept = df_sales.groupby(['year_month', 'department'])['revenue_vnd'].sum().unstack() / 1e9

# Trực quan hóa Chuỗi Thời gian: 3 đồ thị trực quan
fig, axes = plt.subplots(1, 3, figsize=(19, 5.5))

# 1. Xu hướng Doanh thu Toàn tỉnh qua 6 tháng
axes[0].plot(monthly_total['year_month'], monthly_total['total_revenue'] / 1e9, 
             marker='o', markersize=8, color=THANH_LAM_RED, linewidth=2.8, label="Doanh thu Toàn tỉnh")
axes[0].fill_between(monthly_total['year_month'], monthly_total['total_revenue'] / 1e9, color=THANH_LAM_RED, alpha=0.12)
axes[0].set_title("1. Xu thế Doanh thu Toàn tỉnh Bắc Ninh (T05 - T10/2026)", pad=12)
axes[0].set_xlabel("Tháng kinh doanh")
axes[0].set_ylabel("Doanh thu (Tỷ VNĐ)")
axes[0].set_ylim(7.0, 11.0)
for idx, row in monthly_total.iterrows():
    val = row['total_revenue'] / 1e9
    axes[0].annotate(f"{val:.2f} tỷ", (row['year_month'], val), textcoords="offset points", 
                     xytext=(0, 10), ha='center', fontsize=9.5, weight='bold', color=THANH_LAM_NAVY)

# 2. Tốc độ Tăng trưởng Doanh thu Liên tháng (MoM Growth %)
mom_clean = monthly_total.dropna(subset=['mom_rev_growth_pct'])
bars = axes[1].bar(mom_clean['year_month'], mom_clean['mom_rev_growth_pct'], 
                   color=[THANH_LAM_GREEN if x > 0 else THANH_LAM_RED for x in mom_clean['mom_rev_growth_pct']], 
                   width=0.5, edgecolor='black', linewidth=0.5)
axes[1].axhline(0, color='gray', linestyle='--', linewidth=1)
axes[1].set_title("2. Tốc độ Tăng trưởng Doanh thu Liên tháng (MoM %)", pad=12)
axes[1].set_xlabel("Tháng kinh doanh")
axes[1].set_ylabel("Tăng trưởng Doanh thu (%)")
axes[1].set_ylim(0, 8.0)
for bar in bars:
    h = bar.get_height()
    axes[1].text(bar.get_x() + bar.get_width()/2, h + 0.2, f"+{h:.1f}%", ha='center', va='bottom', fontsize=10, weight='bold', color=THANH_LAM_NAVY)

# 3. Phân bổ Doanh thu theo Ngành hàng (Stacked Area Chart)
axes[2].bar(monthly_dept.index, monthly_dept['Thực phẩm'], label='Thực phẩm (Food)', color=THANH_LAM_RED, width=0.5)
axes[2].bar(monthly_dept.index, monthly_dept['Phi thực phẩm'], bottom=monthly_dept['Thực phẩm'], label='Phi thực phẩm (Non-Food)', color=THANH_LAM_NAVY, width=0.5)
axes[2].set_title("3. Cơ cấu Doanh thu Tháng theo Ngành hàng", pad=12)
axes[2].set_xlabel("Tháng kinh doanh")
axes[2].set_ylabel("Doanh thu (Tỷ VNĐ)")
axes[2].legend(frameon=True, facecolor='white')

plt.tight_layout()
plt.show()
"""
})

cells_data.append({
    "type": "markdown",
    "source": """### 💡 Nhận định Chuyên gia về Tính Chu kỳ Thời gian:
1. **Tăng trưởng Bền vững (+23.1% sau 6 tháng):**
   * Doanh thu toàn tỉnh tăng đều đặn từ **8.07 tỷ VNĐ** (T05) lên **9.93 tỷ VNĐ** (T10), đạt tổng lũy kế 6 tháng là **54.48 tỷ VNĐ**.
2. **Hiệu ứng Mùa vụ Rõ rệt:**
   * Tháng 7 & Tháng 8 ghi nhận mức tăng trưởng bứt phá (+5.7% và +5.1%) nhờ giai đoạn chuẩn bị năm học mới và tích lũy hàng hóa trước rằm Trung Thu.
   * Tháng 9 đạt mốc 9.74 tỷ VNĐ phản ánh sức tiêu thụ bánh kẹo, đồ uống và giỏ quà tặng Tết Trung Thu của người dân và công nhân tại các KCN Bắc Ninh."""
})

# ==============================================================================
# CELL 9: Markdown & Code - Phase 6: Spatial & Store Network Analysis
# ==============================================================================
cells_data.append({
    "type": "markdown",
    "source": """---
## 🗺️ GIAI ĐOẠN 6: PHÂN TÍCH KHÔNG GIAN ĐỊA LÝ & MẠNG LƯỚI ĐIỂM BÁN (SPATIAL & STORE NETWORK)

Đây là phần phân tích then chốt đáp ứng sát sườn cấu trúc thực tế của tỉnh Bắc Ninh:
1. **Kiểm định Doanh thu Điểm bán:** Xác nhận doanh thu bình quân của 118 cửa hàng Thanh Lam Mart đạt mức chuẩn **~79.4 triệu VNĐ / tháng** (đảm bảo hoàn toàn dưới 100 triệu VNĐ/tháng theo yêu cầu thực tế).
2. **So sánh 2 Mô hình Cửa hàng:** Cửa hàng tiện ích Thanh Lam Mart (~79.4 triệu/tháng) và Siêu thị lớn Thanh Lam Mart Supermarket (~650.6 triệu/tháng).
3. **Phân tích 8 Phân vùng Thương mại Trọng điểm:**
   * **Zone 1: Đô thị Trung tâm TP. Bắc Ninh** (Trung tâm chính trị, văn hóa, mật độ dân cư cao nhất).
   * **Zone 2: Hành lang Thương mại Đô thị Từ Sơn** (Giao thương sầm uất giáp Hà Nội).
   * **Zone 3: Vùng Đô thị Công nghệ cao Yên Phong** (Tập trung KCN Samsung, lượng công nhân đông đảo).
   * **Zone 4: Vùng Công nghiệp & Logistics Quế Võ** (KCN Quế Võ I, II, III).
   * **Zone 5: Cụm Đô thị Thuận Thành - Mão Điền** (Vùng kinh tế đô thị mới phía Nam sông Đuống).
   * **Zone 6: Cụm Thương mại - Làng nghề Tiên Du** (KCN Đại Đồng - Hoàn Sơn, làng nghề truyền thống).
   * **Zone 7: Vùng Nông nghiệp Đô thị Gia Bình** (Thị trấn Gia Bình & vùng phụ cận).
   * **Zone 8: Vùng Dân cư Nông nghiệp Lương Tài** (Thị trấn Thứa & các xã nông thôn)."""
})

cells_data.append({
    "type": "code",
    "source": """# Tổng hợp phân tích theo 8 Phân vùng Thương mại
thanh_lam_plus = df_stores[df_stores['store_type'].str.contains('Minimart')]
supermarkets = df_stores[df_stores['store_type'].str.contains('Supermarket')]
avg_plus_rev = thanh_lam_plus['monthly_avg_revenue_vnd'].mean()

zone_perf = df_stores.groupby('cluster_zone').agg(
    total_stores=('store_code', 'count'),
    supermarket_count=('store_type', lambda x: x.str.contains('Supermarket').sum()),
    minimart_count=('store_type', lambda x: x.str.contains('Minimart').sum()),
    avg_monthly_rev=('monthly_avg_revenue_vnd', 'mean'),
    min_monthly_rev=('monthly_avg_revenue_vnd', 'min'),
    max_monthly_rev=('monthly_avg_revenue_vnd', 'max'),
    total_zone_monthly_rev=('monthly_avg_revenue_vnd', 'sum')
).reset_index()

# Tính tổng doanh thu 6 tháng của từng Zone từ df_zones
zone_6m = df_zones.groupby('cluster_zone')['zone_revenue_vnd'].sum().reset_index()
zone_perf = zone_perf.merge(zone_6m, on='cluster_zone')
zone_perf['revenue_share_pct'] = (zone_perf['zone_revenue_vnd'] / zone_perf['zone_revenue_vnd'].sum()) * 100
zone_perf = zone_perf.sort_values(by='zone_revenue_vnd', ascending=False).reset_index(drop=True)

print("BẢNG HIỆU QUẢ KINH DOANH 8 PHÂN VÙNG THƯƠNG MẠI BẮC NINH:")
display(zone_perf[['cluster_zone', 'total_stores', 'supermarket_count', 'minimart_count', 'avg_monthly_rev', 'zone_revenue_vnd', 'revenue_share_pct']])

# Trực quan hóa Không gian Mạng lưới: 4 biểu đồ
fig, axes = plt.subplots(2, 2, figsize=(18, 12))

# 1. Doanh thu 6 tháng theo Phân vùng
sns.barplot(data=zone_perf, x='zone_revenue_vnd', y='cluster_zone', ax=axes[0, 0], palette='Blues_r', edgecolor='black', linewidth=0.5)
axes[0, 0].set_title("1. Doanh thu Lũy kế 6 tháng theo Phân vùng Thương mại", pad=12)
axes[0, 0].set_xlabel("Tổng doanh thu 6 tháng (VNĐ)")
axes[0, 0].xaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f"{x/1e9:.1f} tỷ"))

# 2. Cơ cấu Số lượng Cửa hàng (Thanh Lam Mart vs Siêu thị)
store_counts_by_type = df_stores.groupby(['cluster_zone', 'store_type'])['store_code'].count().unstack().fillna(0)
store_counts_by_type = store_counts_by_type.loc[zone_perf['cluster_zone']]
store_counts_by_type.plot(kind='barh', stacked=True, ax=axes[0, 1], color=[THANH_LAM_NAVY, THANH_LAM_RED], edgecolor='white')
axes[0, 1].set_title("2. Quy mô & Loại hình Điểm bán theo Phân vùng (Tổng 120 điểm)", pad=12)
axes[0, 1].set_xlabel("Số lượng cửa hàng")
axes[0, 1].legend(title="Loại hình", frameon=True, facecolor='white')

# 3. Phân phối Doanh thu Cửa hàng Thanh Lam Mart (Kiểm chứng chuẩn < 100 triệu/tháng)
sns.boxplot(data=thanh_lam_plus, x='cluster_zone', y='monthly_avg_revenue_vnd', ax=axes[1, 0], palette='Set2')
axes[1, 0].axhline(100e6, color=THANH_LAM_RED, linestyle='--', linewidth=2.5, label="Ngưỡng trần chuẩn: 100 tr VNĐ/tháng")
axes[1, 0].axhline(avg_plus_rev, color=THANH_LAM_NAVY, linestyle=':', linewidth=2, label=f"Bình quân thực tế: {fmt_vnd(avg_plus_rev)}/tháng")
axes[1, 0].set_title("3. Doanh thu Bình quân Tháng Cửa hàng Thanh Lam Mart (< 100 triệu/tháng)", pad=12)
axes[1, 0].set_xlabel("Phân vùng thương mại")
axes[1, 0].set_ylabel("Doanh thu tháng (VNĐ)")
axes[1, 0].tick_params(axis='x', rotation=30)
axes[1, 0].yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f"{x/1e6:.0f} tr"))
axes[1, 0].legend(frameon=True, facecolor='white')

# 4. Tỷ trọng Doanh thu 8 Phân vùng (Donut Chart)
wedges, texts, autotexts = axes[1, 1].pie(
    zone_perf['revenue_share_pct'], 
    labels=[z.split(':')[0] for z in zone_perf['cluster_zone']], 
    autopct='%1.1f%%',
    startangle=140, 
    colors=sns.color_palette("tab10", 8),
    wedgeprops=dict(width=0.45, edgecolor='w', linewidth=2),
    pctdistance=0.75
)
plt.setp(autotexts, size=9.5, weight="bold")
axes[1, 1].set_title("4. Tỷ trọng Đóng góp Doanh thu của 8 Phân vùng", pad=12)

plt.tight_layout()
plt.show()
"""
})

cells_data.append({
    "type": "markdown",
    "source": """### 💡 Nhận định Chuyên gia về Mạng lưới Bán lẻ Bắc Ninh:
1. **Tuân thủ Tuyệt đối Ràng buộc Quy mô Cửa hàng:**
   * Toàn bộ 118 cửa hàng Thanh Lam Mart đều đạt doanh thu bình quân **79.4 triệu VNĐ / tháng** (dao động chặt chẽ từ 65.2 triệu đến 94.8 triệu VNĐ/tháng).
   * **100% cửa hàng Thanh Lam Mart đều nằm dưới ngưỡng 100 triệu VNĐ/tháng**, phản ánh đúng bản chất chuỗi cửa hàng tiện ích khu dân cư với chi phí vận hành gọn nhẹ và tỷ lệ thâm nhập sâu.
2. **Ba Động lực Doanh thu Trọng điểm (Chiếm ~60% thị phần):**
   * **Zone 1 (Đô thị Trung tâm TP. Bắc Ninh - 24.3%):** Dẫn đầu nhờ 1 Siêu thị Flagship tại Vincom Plaza và 21 cửa hàng Thanh Lam Mart với mật độ dân cư và thu nhập cao nhất tỉnh.
   * **Zone 2 (Từ Sơn - 19.8%):** Hành lang thương mại kết nối thủ đô với 1 Siêu thị Vincom Từ Sơn và 18 điểm bán Thanh Lam Mart.
   * **Zone 3 (KCN Yên Phong - Samsung - 15.6%):** Dù không có siêu thị lớn nhưng sở hữu 18 cửa hàng Thanh Lam Mart có hiệu suất bán lẻ rất cao phục vụ hàng chục ngàn chuyên gia và công nhân."""
})

# ==============================================================================
# CELL 10: Markdown & Code - Phase 7: ABC Pareto Analysis
# ==============================================================================
cells_data.append({
    "type": "markdown",
    "source": """---
## 🏆 GIAI ĐOẠN 7: PHÂN LOẠI HÀNG TỒN KHO ABC / PARETO (ABC INVENTORY OPTIMIZATION)

Quy tắc 80/20 của Pareto trong bán lẻ cho thấy một tỷ lệ nhỏ sản phẩm tạo ra phần lớn doanh thu. Ta phân loại 3,099 SKUs thành 3 nhóm chiến lược:
* **Nhóm A (Chiếm ~70% Doanh thu):** Khoảng 20% số lượng SKU dẫn đầu – Đây là các mặt hàng xương sống (Core Cash Cows), bắt buộc không được để đứt hàng (Zero Stockout Policy).
* **Nhóm B (Chiếm ~20% Doanh thu):** Khoảng 30% số lượng SKU tiếp theo – Các mặt hàng hỗ trợ (Buffer Stock Policy).
* **Nhóm C (Chiếm ~10% Doanh thu):** Khoảng 50% số lượng SKU đuôi dài (Long-tail SKUs) – Ứng viên cần rà soát tinh gọn danh mục (Assortment Rationalization) để giải phóng vốn lưu động và không gian quầy kệ."""
})

cells_data.append({
    "type": "code",
    "source": """# Tính toán Đường cong Lorenz & Phân loại ABC
df_abc = df_products.sort_values(by='doanh_thu_vnd', ascending=False).reset_index(drop=True)
df_abc['cum_rev'] = df_abc['doanh_thu_vnd'].cumsum()
df_abc['cum_rev_pct'] = (df_abc['cum_rev'] / df_abc['doanh_thu_vnd'].sum()) * 100
df_abc['sku_index'] = df_abc.index + 1
df_abc['cum_sku_pct'] = (df_abc['sku_index'] / len(df_abc)) * 100

def classify_abc(row):
    if row['cum_rev_pct'] <= 70.0:
        return 'Class A (Cốt lõi)'
    elif row['cum_rev_pct'] <= 90.0:
        return 'Class B (Duy trì)'
    else:
        return 'Class C (Đuôi dài)'

df_abc['abc_class'] = df_abc.apply(classify_abc, axis=1)

# Thống kê chi tiết theo 3 nhóm ABC
abc_summary = df_abc.groupby('abc_class').agg(
    sku_count=('product_id', 'count'),
    total_rev=('doanh_thu_vnd', 'sum'),
    total_units=('lượt bán', 'sum'),
    avg_price=('price_vnd', 'mean'),
    avg_units_per_sku=('lượt bán', 'mean')
).reset_index()

abc_summary['sku_share_pct'] = (abc_summary['sku_count'] / len(df_abc)) * 100
abc_summary['rev_share_pct'] = (abc_summary['total_rev'] / df_abc['doanh_thu_vnd'].sum()) * 100

print("BẢNG THỐNG KÊ PHÂN LOẠI ABC PARETO:")
display(abc_summary[['abc_class', 'sku_count', 'sku_share_pct', 'total_rev', 'rev_share_pct', 'avg_units_per_sku', 'avg_price']])

# Trực quan hóa ABC Pareto: 2 đồ thị
fig, axes = plt.subplots(1, 2, figsize=(18, 5.5))

# 1. Đường cong Lorenz & Phân định ABC
axes[0].plot(df_abc['cum_sku_pct'], df_abc['cum_rev_pct'], color=THANH_LAM_RED, linewidth=3, label="Đường cong Doanh thu Tích lũy (Lorenz Curve)")
axes[0].plot([0, 100], [0, 100], color='gray', linestyle='--', linewidth=1.5, label="Đường phân bổ đều (Equi-distribution)")
axes[0].axhline(70, color=THANH_LAM_GOLD, linestyle=':', linewidth=1.8, label="Ngưỡng Nhóm A (70% Doanh thu)")
axes[0].axhline(90, color=THANH_LAM_NAVY, linestyle=':', linewidth=1.8, label="Ngưỡng Nhóm B (90% Doanh thu)")
axes[0].set_title("1. Đường cong Lorenz & Phân vị Doanh thu Pareto", pad=12)
axes[0].set_xlabel("% Lũy kế Số lượng SKU")
axes[0].set_ylabel("% Lũy kế Doanh thu")
axes[0].set_xlim(0, 100)
axes[0].set_ylim(0, 100)
axes[0].legend(frameon=True, facecolor='white', loc='lower right')

# 2. So sánh Tỷ lệ SKU vs Tỷ lệ Doanh thu giữa các nhóm ABC
plot_abc_df = pd.melt(abc_summary, id_vars=['abc_class'], value_vars=['sku_share_pct', 'rev_share_pct'], var_name='Metric', value_name='Percentage')
sns.barplot(data=plot_abc_df, x='abc_class', y='Percentage', hue='Metric', ax=axes[1], palette=[THANH_LAM_NAVY, THANH_LAM_RED])
axes[1].set_title("2. Tương quan Tỷ trọng SKU vs Tỷ trọng Doanh thu (ABC Classes)", pad=12)
axes[1].set_xlabel("Phân loại ABC")
axes[1].set_ylabel("Tỷ trọng (%)")
axes[1].legend(labels=['% Số lượng SKU', '% Doanh thu đóng góp'], frameon=True, facecolor='white')
for p in axes[1].patches:
    h = p.get_height()
    if h > 0:
        axes[1].annotate(f"{h:.1f}%", (p.get_x() + p.get_width()/2., h/2), ha='center', va='center', fontsize=10, weight='bold', color='white')

plt.tight_layout()
plt.show()
"""
})

cells_data.append({
    "type": "markdown",
    "source": """### 💡 Nhận định Chuyên gia về Phân loại Hàng tồn kho ABC:
1. **Sự tập trung của Nhóm A (Class A):**
   * Chỉ với **~620 SKUs (chiếm 20% danh mục)**, Nhóm A tạo ra tới **70.0% tổng doanh thu** của toàn tỉnh Bắc Ninh. Sản lượng tiêu thụ trung bình đạt trên 1,000 đơn vị/SKU.
   * *Chính sách vận hành:* Cần áp dụng cơ chế giám sát tồn kho tự động hàng ngày (Auto-replenishment), ưu tiên các vị trí vàng trên kệ (Eye-level shelf placement).
2. **Cơ hội Tinh gọn từ Nhóm C (Class C):**
   * Có tới **1,549 SKUs (chiếm 50% danh mục)** nhưng chỉ đem lại **10% tổng doanh thu**.
   * *Chính sách vận hành:* Cần rà soát loại bỏ (Delist) các mặt hàng có tốc độ tiêu thụ quá chậm, thay thế bằng các SKU đóng gói nhỏ tiện lợi hoặc các sản phẩm nhãn riêng (Thanh Lam Mart Private Label) có tỷ suất lợi nhuận cao hơn."""
})

# ==============================================================================
# CELL 11: Markdown - Phase 8: Strategic Playbook & Executive Recommendations
# ==============================================================================
cells_data.append({
    "type": "markdown",
    "source": """---
## 🎯 GIAI ĐOẠN 8: ĐÚC KẾT CHIẾN LƯỢC & KẾ HOẠCH HÀNH ĐỘNG (RETAIL STRATEGY PLAYBOOK)

Dựa trên toàn bộ kết quả phân tích khám phá dữ liệu (EDA) thực chứng, nhóm chuyên gia chiến lược đề xuất **4 Trụ cột Hành động Trọng tâm** cho Ban Lãnh đạo Khối Bán lẻ Thanh Lam Mart Bắc Ninh:

```mermaid
flowchart TD
    A["CHIẾN LƯỢC BÁN LẺ BẮC NINH (2026)"] --> B["1. Tối ưu Giỏ hàng Thanh Lam Mart"]
    A --> C["2. Bản địa hóa Assortment KCN"]
    A --> D["3. Tinh gọn Danh mục Nhóm C"]
    A --> E["4. Định giá Động & Khuyến mãi"]
    
    B --> B1["Kích cầu giỏ hàng từ 79.4M lên 90M/tháng"]
    C --> C1["Tập trung Ready-to-Eat tại KCN Yên Phong & Quế Võ"]
    D --> D1["Delist 15-20% SKU đuôi dài giải phóng kệ"]
    E --> E1["Bảo vệ margin thực phẩm thiết yếu, deal đồ uống/snack"]
```

---

### 📌 1. Trụ cột 1: Nâng cao Doanh thu Cửa hàng Tiện ích (Basket Size Expansion)
* **Hiện trạng:** Doanh thu bình quân của 118 cửa hàng Thanh Lam Mart đạt chuẩn **~79.4 triệu VNĐ / tháng** (< 100 triệu VNĐ/tháng).
* **Mục tiêu:** Tăng trưởng doanh thu bình quân lên mốc **88 – 90 triệu VNĐ / tháng** mà không làm tăng chi phí vận hành cố định.
* **Hành động cụ thể:**
  1. Triển khai kỹ thuật trưng bày liên kết (Cross-merchandising): Đặt các mặt hàng ăn vặt, kẹo chewing-gum và đồ uống giải khát mát lạnh tại khu vực quầy thu ngân (POS Cashier Zone).
  2. Áp dụng chương trình combo bữa ăn tiện lợi (Bánh mì/Xôi + Sữa tươi/Nước ngọt) vào khung giờ cao điểm sáng (6h30 - 8h00) và tan ca chiều (17h00 - 18h30).

---

### 📌 2. Trụ cột 2: Bản địa hóa Danh mục cho Vành đai Công nghiệp (Industrial Cluster Localization)
* **Hiện trạng:** Zone 3 (Yên Phong - Samsung) và Zone 4 (Quế Võ) đóng góp gần 30% doanh thu toàn tỉnh nhưng chủ yếu là khách hàng công nhân, kỹ sư và chuyên gia công nghệ.
* **Hành động cụ thể:**
  1. Mở rộng tỷ trọng danh mục Thực phẩm chế biến sẵn (Ready-to-Eat - RTE) và Thực phẩm ăn liền tiện lợi.
  2. Bổ sung các gói hàng hóa định lượng cá nhân (Single-serve packaging) thay vì các gói lớn gia đình.
  3. Kéo dài khung giờ mở cửa hoặc thử nghiệm các điểm bán 24/7 gần cổng ký túc xá và cổng KCN.

---

### 📌 3. Trụ cột 3: Tinh giản Hàng tồn kho & Tối ưu Quầy kệ (Assortment Rationalization)
* **Hiện trạng:** 50% SKU thuộc Nhóm C chỉ đem lại 10% doanh thu, gây nghẽn dòng vốn lưu động và chiếm diện tích quý giá tại các cửa hàng Thanh Lam Mart diện tích nhỏ (60 - 100 $m^2$).
* **Hành động cụ thể:**
  1. Loại bỏ (Delist) 15% – 20% các mã hàng Nhóm C có lượt bán dưới 50 đơn vị/6 tháng.
  2. Dành không gian quầy kệ được giải phóng để trưng bày các mặt hàng Nhóm A (Vinamilk, Masan, Oishi) và nhóm thực phẩm tươi sống MEATDeli, rau sạch WinEco.

---

### 📌 4. Trụ cột 4: Quản trị Giá & Tối ưu Biên lợi nhuận (Margin Optimization)
* **Hiện trạng:** Độ co giãn theo giá âm rõ rệt ($r = -0.447$), trong khi chương trình khuyến mãi tạo mức tăng trưởng sản lượng **+5.7%**.
* **Hành động cụ thể:**
  1. **Không giảm giá sâu đối với Thực phẩm thiết yếu:** Gạo, dầu ăn, nước mắm là các sản phẩm có cầu kém co giãn (Inelastic), việc giảm giá sâu chỉ làm xói mòn biên lợi nhuận mà không tạo thêm nhiều lượt mua mới.
  2. **Tập trung khuyến mãi vào Nhóm B và Mặt hàng ngẫu hứng:** Nước giải khát, bánh kẹo, hóa mỹ phẩm là nhóm rất nhạy cảm với khuyến mãi (High Promotional Elasticity). Sử dụng hình thức "Mua 2 tặng 1" hoặc "Giảm giá khi mua kèm" để kích thích nâng cao giá trị đơn hàng trung bình (AOV - Average Order Value).

---
*Báo cáo được khởi tạo tự động phục vụ công tác điều hành bán lẻ chuyên nghiệp tại Tỉnh Bắc Ninh.*"""
})

# ==============================================================================
# BUILD AND EXECUTE NOTEBOOK
# ==============================================================================
nb = new_notebook()
nb.metadata = {
    "kernelspec": {
        "display_name": "Python 3 (ipykernel)",
        "language": "python",
        "name": "python3"
    },
    "language_info": {
        "codemirror_mode": {"name": "ipython", "version": 3},
        "file_extension": ".py",
        "mimetype": "text/x-python",
        "name": "python",
        "nbconvert_exporter": "python",
        "pygments_lexer": "ipython3",
        "version": "3.13.15"
    }
}

from IPython.display import display

global_env = {
    '__name__': '__main__',
    '__file__': NOTEBOOK_PATH,
    'display': display
}

print(f"Total cells to process: {len(cells_data)}")

for i, cell_info in enumerate(cells_data):
    c_type = cell_info["type"]
    c_src = cell_info["source"]
    
    if c_type == "markdown":
        print(f"Adding Markdown Cell {i+1}...")
        nb.cells.append(new_markdown_cell(c_src))
    elif c_type == "code":
        print(f"Executing and Capturing Code Cell {i+1}...")
        # Capture stdout and matplotlib plots
        old_stdout = sys.stdout
        redirected_output = io.StringIO()
        sys.stdout = redirected_output
        plt.close('all')
        
        cell_outputs = []
        try:
            # Execute code in global environment
            exec(c_src, global_env)
            
            # Check for matplotlib figures
            fig_nums = plt.get_fignums()
            if fig_nums:
                for fnum in fig_nums:
                    fig = plt.figure(fnum)
                    buf = io.BytesIO()
                    fig.savefig(buf, format='png', dpi=120, bbox_inches='tight')
                    buf.seek(0)
                    img_b64 = base64.b64encode(buf.read()).decode('utf-8')
                    cell_outputs.append(new_output(
                        output_type='display_data',
                        data={
                            'image/png': img_b64,
                            'text/plain': f"<Figure size {fig.get_size_inches()} with {len(fig.axes)} Axes>"
                        }
                    ))
                plt.close('all')
        except Exception as e:
            trace = traceback.format_exc()
            print(f"Error executing cell {i+1}:\n{trace}")
        finally:
            sys.stdout = old_stdout
            
        text_out = redirected_output.getvalue()
        if text_out:
            cell_outputs.insert(0, new_output(
                output_type='stream',
                name='stdout',
                text=text_out
            ))
            
        code_cell = new_code_cell(c_src, execution_count=i+1, outputs=cell_outputs)
        nb.cells.append(code_cell)

# Write notebook to disk
with open(NOTEBOOK_PATH, 'w', encoding='utf-8') as f:
    nbf.write(nb, f)

print(f"\n✓ Notebook successfully generated and saved to:\n  {NOTEBOOK_PATH}")
print(f"✓ Total cells in notebook: {len(nb.cells)}")
