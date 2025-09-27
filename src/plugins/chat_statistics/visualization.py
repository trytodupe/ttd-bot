"""
Visualization interface for chat statistics

This module provides chart generation functionality for chat statistics.
"""

import asyncio
from functools import partial
from io import BytesIO
from typing import Optional
import concurrent.futures

try:
    import matplotlib.pyplot as plt
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False

from .statistics import ChatStatistics, ActiveStatistics


def setup_chinese_font():
    """Setup Chinese font for matplotlib - only use bundled font or fail"""
    if not MATPLOTLIB_AVAILABLE:
        return False
    
    # Get path to bundled font
    from pathlib import Path
    plugin_dir = Path(__file__).parent
    font_path = plugin_dir / "assets" / "MonuTitl-0.96Cond.ttf"
    
    if not font_path.exists():
        return False
    
    try:
        from matplotlib import font_manager
        # Register the bundled font
        font_manager.fontManager.addfont(str(font_path))
        
        # Use font properties directly
        font_prop = font_manager.FontProperties(fname=str(font_path))
        plt.rcParams['font.family'] = font_prop.get_name()
        plt.rcParams['axes.unicode_minus'] = False
        return True
    except Exception:
        return False


def _generate_chat_chart(stats: ChatStatistics, user_display: str = None) -> Optional[bytes]:
    """Generate chat statistics chart"""
    if not MATPLOTLIB_AVAILABLE:
        return None
    
    # Only proceed if custom font is available
    if not setup_chinese_font():
        return None
    
    # Prepare data
    hours = list(range(24))
    counts = [stats.hourly_distribution.get(h, 0) for h in hours]
    
    # Create figure
    fig, ax = plt.subplots(figsize=(12, 6))
    
    # Create gradient colors based on hour (12pm brightest, 0am/24pm darkest)
    # Calculate brightness factor for each hour (sine wave peaking at 12)
    import math
    colors = []
    for hour in hours:
        # Create brightness factor: 12pm = 1.0 (brightest), 0am/24pm = 0.3 (darkest)
        brightness = 0.3 + 0.7 * (1 + math.cos(2 * math.pi * (hour - 12) / 24)) / 2
        
        # Apply brightness to base color #4A90E2
        base_r, base_g, base_b = 0x4A/255, 0x90/255, 0xE2/255
        
        # Blend with lighter version for brightness
        light_r, light_g, light_b = 0.8, 0.95, 1.0  # Light blue-white
        
        r = base_r + (light_r - base_r) * (brightness - 0.3) / 0.7
        g = base_g + (light_g - base_g) * (brightness - 0.3) / 0.7  
        b = base_b + (light_b - base_b) * (brightness - 0.3) / 0.7
        
        colors.append((r, g, b))
    
    # Create bar chart with gradient colors
    bars = ax.bar(hours, counts, color=colors, alpha=0.9, edgecolor='#2E5C8A', linewidth=1)
    
    # Customize chart
    user_part = f"{user_display}的" if user_display else ""
    ax.set_title(f'{user_part}过去{stats.days}天聊天分布 (共{stats.total_messages}条消息)', 
                 fontsize=16, fontweight='bold', pad=20)
    ax.set_xlabel('小时', fontsize=12)
    ax.set_ylabel('消息数量', fontsize=12)
    
    # Set x-axis
    ax.set_xticks(hours)
    ax.set_xticklabels([f'{h:02d}' for h in hours])
    
    # Add value labels on bars
    for bar, count in zip(bars, counts):
        if count > 0:
            height = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2., height + max(counts) * 0.01,
                   f'{count}', ha='center', va='bottom', fontsize=9)
    
    # Style
    ax.grid(True, alpha=0.3, axis='y')
    ax.set_axisbelow(True)
    
    # Tight layout
    plt.tight_layout()
    
    # Save to bytes
    img_buffer = BytesIO()
    plt.savefig(img_buffer, format='PNG', dpi=150, bbox_inches='tight')
    plt.close(fig)
    
    img_buffer.seek(0)
    return img_buffer.getvalue()


def _generate_active_chart(stats: ActiveStatistics, user_display: str = None) -> Optional[bytes]:
    """Generate active statistics chart"""
    if not MATPLOTLIB_AVAILABLE:
        return None
    
    # Only proceed if custom font is available
    if not setup_chinese_font():
        return None
    
    # Prepare data
    hours = list(range(24))
    active_counts = stats.get_hourly_active_counts()
    counts = [active_counts.get(h, 0) for h in hours]
    percentages = [stats.get_hourly_percentages().get(h, 0) for h in hours]
    
    # Create figure
    fig, ax = plt.subplots(figsize=(12, 6))
    
    # Create gradient colors based on hour (12pm brightest, 0am/24pm darkest)
    # Calculate brightness factor for each hour (sine wave peaking at 12)
    import math
    colors = []
    for hour in hours:
        # Create brightness factor: 12pm = 1.0 (brightest), 0am/24pm = 0.3 (darkest)
        brightness = 0.3 + 0.7 * (1 + math.cos(2 * math.pi * (hour - 12) / 24)) / 2
        
        # Apply brightness to base color #4A90E2
        base_r, base_g, base_b = 0x4A/255, 0x90/255, 0xE2/255
        
        # Blend with lighter version for brightness
        light_r, light_g, light_b = 0.8, 0.95, 1.0  # Light blue-white
        
        r = base_r + (light_r - base_r) * (brightness - 0.3) / 0.7
        g = base_g + (light_g - base_g) * (brightness - 0.3) / 0.7  
        b = base_b + (light_b - base_b) * (brightness - 0.3) / 0.7
        
        colors.append((r, g, b))
    
    # Create bar chart with gradient colors
    bars = ax.bar(hours, counts, color=colors, alpha=0.9, edgecolor='#2E5C8A', linewidth=1)
    
    # Customize chart
    total_active_hours = len([h for h, count in active_counts.items() if count > 0])
    user_part = f"{user_display}的" if user_display else ""
    ax.set_title(f'{user_part}过去{stats.days}天活跃时间分布 (共{total_active_hours}个活跃小时)', 
                 fontsize=16, fontweight='bold', pad=20)
    ax.set_xlabel('小时', fontsize=12)
    ax.set_ylabel('活跃天数', fontsize=12)
    
    # Set x-axis
    ax.set_xticks(hours)
    ax.set_xticklabels([f'{h:02d}' for h in hours])
    
    # Add value labels on bars
    for bar, count, pct in zip(bars, counts, percentages):
        if count > 0:
            height = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2., height + max(counts) * 0.01,
                   f'{count}\n({pct:.1f}%)', ha='center', va='bottom', fontsize=9)
    
    # Style
    ax.grid(True, alpha=0.3, axis='y')
    ax.set_axisbelow(True)
    
    # Tight layout
    plt.tight_layout()
    
    # Save to bytes
    img_buffer = BytesIO()
    plt.savefig(img_buffer, format='PNG', dpi=150, bbox_inches='tight')
    plt.close(fig)
    
    img_buffer.seek(0)
    return img_buffer.getvalue()


async def generate_chat_chart(stats: ChatStatistics, user_display: str = None) -> Optional[bytes]:
    """Generate chat statistics visualization chart"""
    if not MATPLOTLIB_AVAILABLE:
        return None
    
    loop = asyncio.get_running_loop()
    pfunc = partial(_generate_chat_chart, stats, user_display)
    
    with concurrent.futures.ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, pfunc)


async def generate_active_chart(stats: ActiveStatistics, user_display: str = None) -> Optional[bytes]:
    """Generate active statistics visualization chart"""
    if not MATPLOTLIB_AVAILABLE:
        return None
    
    loop = asyncio.get_running_loop()
    pfunc = partial(_generate_active_chart, stats, user_display)
    
    with concurrent.futures.ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, pfunc)


def _generate_combined_chart(chat_stats: ChatStatistics, active_stats: ActiveStatistics, user_display: str = None) -> Optional[bytes]:
    """Generate combined chat and active statistics chart with flat design"""
    if not MATPLOTLIB_AVAILABLE:
        return None
    
    # Only proceed if custom font is available
    if not setup_chinese_font():
        return None
    
    # Prepare data
    hours = list(range(24))
    chat_counts = [chat_stats.hourly_distribution.get(h, 0) for h in hours]
    active_counts = [active_stats.get_hourly_active_counts().get(h, 0) for h in hours]
    
    # Create figure with flat design
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8))
    fig.patch.set_facecolor('#FAFAFA')  # Light gray background
    
    # Create gradient colors for both charts based on hour (12pm brightest, 0am/24pm darkest)
    import math
    
    chat_colors = []
    active_colors = []
    text_color = '#2C3E50'      # Dark blue-gray
    
    for hour in hours:
        # Create brightness factor: 12pm = 1.0 (brightest), 0am/24pm = 0.3 (darkest)
        brightness = 0.3 + 0.7 * (1 + math.cos(2 * math.pi * (hour - 12) / 24)) / 2
        
        # Chat colors - base blue #3498DB
        base_r, base_g, base_b = 0x34/255, 0x98/255, 0xDB/255
        light_r, light_g, light_b = 0.7, 0.9, 1.0  # Light blue-white
        
        r = base_r + (light_r - base_r) * (brightness - 0.3) / 0.7
        g = base_g + (light_g - base_g) * (brightness - 0.3) / 0.7  
        b = base_b + (light_b - base_b) * (brightness - 0.3) / 0.7
        chat_colors.append((r, g, b))
        
        # Active colors - base red #E74C3C  
        base_r, base_g, base_b = 0xE7/255, 0x4C/255, 0x3C/255
        light_r, light_g, light_b = 1.0, 0.8, 0.8  # Light red-white
        
        r = base_r + (light_r - base_r) * (brightness - 0.3) / 0.7
        g = base_g + (light_g - base_g) * (brightness - 0.3) / 0.7  
        b = base_b + (light_b - base_b) * (brightness - 0.3) / 0.7
        active_colors.append((r, g, b))
    
    # Top chart - Chat messages with gradient
    bars1 = ax1.bar(hours, chat_counts, color=chat_colors, alpha=0.9, width=0.8)
    
    user_part = f"{user_display}的" if user_display else ""
    ax1.set_title(f'{user_part}过去{chat_stats.days}天统计概览', 
                  fontsize=18, fontweight='500', color=text_color, pad=15)
    ax1.set_ylabel('消息数量', fontsize=12, color=text_color)
    
    # Add total message count as subtitle
    ax1.text(0.02, 0.95, f'总消息: {chat_stats.total_messages}条', 
             transform=ax1.transAxes, fontsize=11, color='#7F8C8D',
             verticalalignment='top')
    
    # Bottom chart - Active days with gradient
    bars2 = ax2.bar(hours, active_counts, color=active_colors, alpha=0.9, width=0.8)
    ax2.set_ylabel('活跃天数', fontsize=12, color=text_color)
    ax2.set_xlabel('小时', fontsize=12, color=text_color)
    
    # Add total active hours as subtitle
    total_active_hours = len([h for h, count in active_stats.get_hourly_active_counts().items() if count > 0])
    ax2.text(0.02, 0.95, f'活跃小时: {total_active_hours}个', 
             transform=ax2.transAxes, fontsize=11, color='#7F8C8D',
             verticalalignment='top')
    
    # Style both charts with flat design
    for ax, bars, max_val in [(ax1, bars1, max(chat_counts) if chat_counts else 1), 
                              (ax2, bars2, max(active_counts) if active_counts else 1)]:
        # Remove spines for flat look
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color('#BDC3C7')
        ax.spines['bottom'].set_color('#BDC3C7')
        
        # Minimal grid
        ax.grid(True, alpha=0.3, axis='y', linestyle='-', linewidth=0.5)
        ax.set_axisbelow(True)
        
        # Clean axis
        ax.set_xticks(hours[::2])  # Show every 2 hours
        ax.set_xticklabels([f'{h:02d}' for h in hours[::2]], fontsize=10, color=text_color)
        ax.tick_params(colors=text_color, length=0)  # Remove tick marks
        
        # Add value labels only on significant bars
        for bar, count in zip(bars, chat_counts if ax == ax1 else active_counts):
            if count > max_val * 0.1:  # Only show if > 10% of max
                height = bar.get_height()
                ax.text(bar.get_x() + bar.get_width()/2., height + max_val * 0.02,
                       f'{int(count)}', ha='center', va='bottom', 
                       fontsize=9, color=text_color, fontweight='400')
    
    # Tight layout with minimal padding
    plt.tight_layout(pad=2.0)
    
    # Save with flat design optimization
    img_buffer = BytesIO()
    plt.savefig(img_buffer, format='PNG', dpi=150, bbox_inches='tight', 
                facecolor='#FAFAFA', edgecolor='none')
    plt.close(fig)
    
    img_buffer.seek(0)
    return img_buffer.getvalue()


async def generate_combined_chart(chat_stats: ChatStatistics, active_stats: ActiveStatistics, user_display: str = None) -> Optional[bytes]:
    """Generate combined chart with both chat and active statistics"""
    if not MATPLOTLIB_AVAILABLE:
        return None
    
    loop = asyncio.get_running_loop()
    pfunc = partial(_generate_combined_chart, chat_stats, active_stats, user_display)
    
    with concurrent.futures.ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, pfunc)

