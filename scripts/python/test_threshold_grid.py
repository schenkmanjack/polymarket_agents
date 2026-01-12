"""
Test threshold-based backtesting strategy with grid search.

Runs grid search separately for 15-minute and 1-hour markets,
displaying results in threshold vs margin grids and saving heatmap plots.
"""
import sys
import os
import argparse
import json
from datetime import datetime, timezone
from typing import Optional, Union, Tuple, Dict
import pandas as pd
import numpy as np

# Try to import plotting libraries, but make them optional
try:
    import matplotlib
    matplotlib.use('Agg')  # Use non-interactive backend
    import matplotlib.pyplot as plt
    import seaborn as sns
    PLOTTING_AVAILABLE = True
except ImportError:
    PLOTTING_AVAILABLE = False
    print("Warning: matplotlib/seaborn not available. Plots will not be generated.")

# Try to import plotly for interactive plots
try:
    import plotly.graph_objects as go
    import plotly.express as px
    from plotly.subplots import make_subplots
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False
    make_subplots = None
    print("Warning: plotly not available. Interactive plots will not be generated.")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from agents.backtesting.threshold_backtester import ThresholdBacktester
from agents.backtesting.backtesting_utils import calculate_kelly_fraction, calculate_kelly_roi
from dotenv import load_dotenv

load_dotenv()


def convert_to_json_serializable(obj):
    """Recursively convert numpy types and other non-JSON-serializable types to native Python types."""
    import numpy as np
    
    # Check for numpy integer types (using base class for NumPy 2.0 compatibility)
    if isinstance(obj, np.integer):
        return int(obj)
    # Check for numpy floating point types (np.float_ removed in NumPy 2.0)
    elif isinstance(obj, np.floating):
        return float(obj)
    # Check for numpy boolean types
    elif isinstance(obj, np.bool_):
        return bool(obj)
    # Check for numpy arrays
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    # Handle dictionaries recursively
    elif isinstance(obj, dict):
        return {key: convert_to_json_serializable(value) for key, value in obj.items()}
    # Handle lists and tuples recursively
    elif isinstance(obj, (list, tuple)):
        return [convert_to_json_serializable(item) for item in obj]
    else:
        return obj


def create_heatmap(
    df: pd.DataFrame,
    metric: str,
    market_type: str,
    output_dir: str = None
) -> Union[str, Tuple[str, str], None]:
    """Create and save a heatmap visualization of the grid results.
    
    Returns:
        - str: Path to PNG file if only PNG is created
        - Tuple[str, str]: (PNG path, HTML path) if both PNG and HTML are created
        - None: If plotting is not available or df is empty
    """
    if not PLOTTING_AVAILABLE:
        return None
    
    if df.empty:
        return None
    
    # Create pivot table
    pivot = df.pivot_table(
        values=metric,
        index='threshold',
        columns='margin',
        aggfunc='first'
    )
    
    # Format threshold and margin labels to 3 decimal places
    pivot.index = [f"{x:.3f}" for x in pivot.index]
    pivot.columns = [f"{x:.3f}" for x in pivot.columns]
    
    # Set up the plot with larger figure size
    plt.figure(figsize=(16, 10))
    
    # Choose colormap based on metric
    if metric == 'avg_roi':
        cmap = 'RdYlGn'  # Red-Yellow-Green (red=bad, green=good)
        center = 0.0
        fmt_str = '.2f'  # 2 decimal places for ROI
    elif metric == 'win_rate':
        cmap = 'RdYlGn'  # Red-Yellow-Green
        center = 0.5
        fmt_str = '.2f'  # 2 decimal places for win rate
    else:
        cmap = 'viridis'
        center = None
        fmt_str = '.2f'
    
    # Create a mask for NaN values
    mask = pivot.isna()
    
    # For large grids, reduce annotation density
    num_cells = pivot.size
    num_rows, num_cols = pivot.shape
    
    # Decide annotation strategy based on grid size
    if num_cells < 100:
        # Small grid: annotate all cells
        annot_data = pivot
        annotate = True
    elif num_cells < 400:
        # Medium grid: annotate significant values only
        if metric == 'avg_roi':
            # Only annotate if |value| > 0.03
            annot_data = pivot.where((pivot.abs() > 0.03) | pivot.isna())
        elif metric == 'win_rate':
            # For win rate, annotate all (it's usually between 0 and 1, easier to read)
            annot_data = pivot
        else:
            annot_data = pivot
        annotate = True
    else:
        # Large grid: don't annotate, just show colors
        annot_data = None
        annotate = False
    
    # Create heatmap
    sns.heatmap(
        pivot,
        annot=annotate,
        fmt=fmt_str if annotate else None,
        cmap=cmap,
        center=center,
        cbar_kws={'label': metric.replace('_', ' ').title()},
        linewidths=0.2 if num_cells > 200 else 0.3,
        linecolor='gray',
        mask=mask,
        square=False,
        annot_kws={'size': 7, 'weight': 'normal'} if annotate else None  # Smaller font for annotations
    )
    
    plt.title(f'{market_type.upper()} Markets - {metric.replace("_", " ").title()}', 
              fontsize=16, fontweight='bold', pad=20)
    plt.xlabel('Margin', fontsize=12, fontweight='bold')
    plt.ylabel('Threshold', fontsize=12, fontweight='bold')
    
    # Rotate x-axis labels for better readability
    plt.xticks(rotation=45, ha='right')
    plt.yticks(rotation=0)
    
    plt.tight_layout()
    
    # Save the plot
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        filename = f'{market_type}_{metric}_heatmap.png'
        filepath = os.path.join(output_dir, filename)
        plt.savefig(filepath, dpi=150, bbox_inches='tight')
        plt.close()
        
        # Also create interactive HTML version with hover tooltips
        if PLOTLY_AVAILABLE:
            html_filepath = create_interactive_heatmap(
                df, metric, market_type, output_dir
            )
            if html_filepath:
                return filepath, html_filepath
        
        return filepath
    else:
        plt.close()
        return None


def create_interactive_heatmap(
    df: pd.DataFrame,
    metric: str,
    market_type: str,
    output_dir: str = None
) -> Optional[str]:
    """Create an interactive HTML heatmap with hover tooltips, clickable histogram, and dollar_amount slider using Plotly."""
    if not PLOTLY_AVAILABLE:
        return None
    
    if df.empty:
        return None
    
    # Get individual trades data if available
    individual_trades = df.attrs.get('individual_trades', {}) if hasattr(df, 'attrs') else {}
    
    # Check if dollar_amount column exists
    has_dollar_amount = 'dollar_amount' in df.columns
    
    if has_dollar_amount:
        # Get unique dollar_amount values and sort them
        dollar_amount_values = sorted(df['dollar_amount'].unique().tolist())
        default_dollar_amount = dollar_amount_values[0]  # Use first (minimum) as default
        
        # Get all unique thresholds and margins from FULL dataset (for consistent grid structure)
        all_thresholds = sorted(df['threshold'].unique().tolist())
        all_margins = sorted(df['margin'].unique().tolist())
        
        # Filter df by default dollar_amount for initial display
        df_filtered = df[df['dollar_amount'] == default_dollar_amount].copy()
    else:
        # No dollar_amount column - use all data
        dollar_amount_values = []
        default_dollar_amount = None
        all_thresholds = sorted(df['threshold'].unique().tolist())
        all_margins = sorted(df['margin'].unique().tolist())
        df_filtered = df.copy()
    
    # Create pivot table from filtered data
    pivot = df_filtered.pivot_table(
        values=metric,
        index='threshold',
        columns='margin',
        aggfunc='first'
    )
    
    # Reindex to ensure consistent grid structure (fill missing with NaN)
    if has_dollar_amount:
        pivot = pivot.reindex(index=all_thresholds, columns=all_margins)
    
    # Format values for display
    if metric == 'avg_roi':
        fmt_str = '.2%'  # Percentage format for ROI
        hover_template = 'Threshold: %{y}<br>Margin: %{x}<br>Avg ROI: %{z:.2%}<extra></extra>'
    elif metric == 'win_rate':
        fmt_str = '.1%'  # Percentage format for win rate
        hover_template = 'Threshold: %{y}<br>Margin: %{x}<br>Win Rate: %{z:.1%}<extra></extra>'
    elif metric == 'kelly_roi':
        fmt_str = '.2%'  # Percentage format for Kelly ROI
        hover_template = 'Threshold: %{y}<br>Margin: %{x}<br>Kelly ROI: %{z:.2%}<extra></extra>'
    else:
        fmt_str = '.2f'
        hover_template = 'Threshold: %{y}<br>Margin: %{x}<br>Value: %{z:.2f}<extra></extra>'
    
    # Store original numeric values for hover tooltips
    original_thresholds = pivot.index.values
    original_margins = pivot.columns.values
    
    # Format labels to 3 decimal places for display
    threshold_labels = [f"{x:.3f}" for x in original_thresholds]
    margin_labels = [f"{x:.3f}" for x in original_margins]
    
    # Create hover text with formatted values - use same values as pivot (for consistency)
    hover_texts = []
    for i, row in enumerate(pivot.values):
        hover_row = []
        for j, val in enumerate(row):
            if pd.notna(val):
                dollar_amt_str = f"<br>Dollar Amount: ${int(default_dollar_amount)}" if has_dollar_amount else ""
                if metric == 'avg_roi':
                    hover_row.append(f"Threshold: {original_thresholds[i]:.3f}<br>Margin: {original_margins[j]:.3f}{dollar_amt_str}<br>Avg ROI: {val:.2%}")
                elif metric == 'win_rate':
                    hover_row.append(f"Threshold: {original_thresholds[i]:.3f}<br>Margin: {original_margins[j]:.3f}{dollar_amt_str}<br>Win Rate: {val:.1%}")
                elif metric == 'kelly_roi':
                    hover_row.append(f"Threshold: {original_thresholds[i]:.3f}<br>Margin: {original_margins[j]:.3f}{dollar_amt_str}<br>Kelly ROI: {val:.2%}")
                else:
                    hover_row.append(f"Threshold: {original_thresholds[i]:.3f}<br>Margin: {original_margins[j]:.3f}{dollar_amt_str}<br>Value: {val:.2f}")
            else:
                hover_row.append("")
        hover_texts.append(hover_row)
    
    # Create subplot with heatmap on left and histogram on right
    fig = make_subplots(
        rows=1, cols=2,
        column_widths=[0.6, 0.4],
        subplot_titles=(
            f'{market_type.upper()} Markets - {metric.replace("_", " ").title()}',
            'ROI Distribution (Click a cell)'
        ),
        horizontal_spacing=0.15
    )
    
    # Format cell text to match hover format (percentage for ROI metrics)
    if metric == 'avg_roi' or metric == 'kelly_roi':
        cell_text = [[f"{(val * 100):.2f}%" if pd.notna(val) else "" for val in row] for row in pivot.values]
    elif metric == 'win_rate':
        cell_text = [[f"{(val * 100):.1f}%" if pd.notna(val) else "" for val in row] for row in pivot.values]
    else:
        cell_text = [[f"{val:.2f}" if pd.notna(val) else "" for val in row] for row in pivot.values]
    
    # Add heatmap to left subplot
    heatmap = go.Heatmap(
        z=pivot.values,
        x=margin_labels,
        y=threshold_labels,
        colorscale='RdYlGn',
        text=cell_text,
        texttemplate='%{text}',
        textfont={"size": 8},
        hovertext=hover_texts,
        hovertemplate='%{hovertext}<extra></extra>',
        colorbar=dict(title=metric.replace('_', ' ').title(), len=0.6, y=0.5),
        showscale=True
    )
    fig.add_trace(heatmap, row=1, col=1)
    
    # Add empty histogram placeholder to right subplot
    fig.add_trace(
        go.Histogram(x=[], nbinsx=50, name='ROI Distribution'),
        row=1, col=2
    )
    
    # Update axes
    fig.update_xaxes(title_text='Margin', row=1, col=1)
    fig.update_yaxes(title_text='Threshold', row=1, col=1)
    fig.update_xaxes(title_text='ROI', row=1, col=2)
    fig.update_yaxes(title_text='Count', row=1, col=2)
    
    # Update layout
    fig.update_layout(
        width=1800,
        height=800,
        font=dict(size=12),
        template='plotly_white'
    )
    
    # Save the HTML file first
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        filename = f'{market_type}_{metric}_heatmap.html'
        filepath = os.path.join(output_dir, filename)
        
        # Write HTML with embedded data and JavaScript
        html_content = fig.to_html(include_plotlyjs='cdn', div_id='plotly-div')
        
        # Inject dollar amount slider before the plotly div (if dollar_amount exists)
        dollar_amount_slider_html = ""
        if has_dollar_amount:
            dollar_amount_slider_html = f"""
    <div style="margin: 20px; padding: 10px; background-color: #f0f0f0; border-radius: 5px;">
        <label for="dollarAmountSlider" style="font-weight: bold; margin-right: 10px;">Dollar Amount:</label>
        <input type="range" id="dollarAmountSlider" min="0" max="{len(dollar_amount_values)-1}" value="0" 
               step="1" style="width: 300px;">
        <span id="dollarAmountValue" style="margin-left: 10px; font-weight: bold;">${int(default_dollar_amount)}</span>
    </div>
"""
            # Insert slider before the plotly div
            slider_insertion_point = html_content.find('<div id="plotly-div"')
            if slider_insertion_point != -1:
                html_content = html_content[:slider_insertion_point] + dollar_amount_slider_html + html_content[slider_insertion_point:]
        
        # Inject JavaScript callback for click events
        # Convert numpy arrays to lists for JSON serialization
        thresholds_list = original_thresholds.tolist() if hasattr(original_thresholds, 'tolist') else list(original_thresholds)
        margins_list = original_margins.tolist() if hasattr(original_margins, 'tolist') else list(original_margins)
        
        # Convert tuple keys to string keys for JSON
        # Handle both (threshold, margin) and (threshold, margin, dollar_amount) keys
        # Also calculate Kelly fraction for each combination
        individual_trades_json = {}
        kelly_fractions_json = {}
        
        # Need to get full trade objects, not just ROI values, for Kelly calculation
        # Check if individual_trades contains full trade objects or just ROI values
        sample_value = next(iter(individual_trades.values())) if individual_trades else None
        has_full_trades = sample_value and isinstance(sample_value, list) and len(sample_value) > 0 and isinstance(sample_value[0], dict) and 'roi' in sample_value[0]
        
        for key_tuple, trade_data in individual_trades.items():
            # Convert tuple elements to native Python types first (NumPy 2.0 compatibility)
            key_tuple_converted = tuple(convert_to_json_serializable(k) for k in key_tuple)
            
            if len(key_tuple_converted) == 2:
                # Old format: (threshold, margin)
                thresh, marg = key_tuple_converted
                key = f"{thresh:.6f},{marg:.6f}"
            elif len(key_tuple_converted) == 3:
                # New format: (threshold, margin, dollar_amount)
                thresh, marg, dollar_amt = key_tuple_converted
                key = f"{thresh:.6f},{marg:.6f},{dollar_amt:.0f}"
            else:
                continue
            
            # Extract ROI values (either from list of ROI floats or list of trade dicts)
            if has_full_trades and isinstance(trade_data, list) and len(trade_data) > 0 and isinstance(trade_data[0], dict):
                roi_values = [t.get('roi', 0.0) for t in trade_data]
                trades_for_kelly = trade_data  # Use full trade objects
            else:
                # Assume trade_data is a list of ROI values
                roi_values = convert_to_json_serializable(trade_data) if isinstance(trade_data, list) else [trade_data]
                # Create minimal trade dicts for Kelly calculation from ROI values
                trades_for_kelly = [{'roi': float(roi), 'is_win': float(roi) > 0, 'dollar_amount': 4000.0} for roi in roi_values]
            
            individual_trades_json[key] = convert_to_json_serializable(roi_values)
            
            # Calculate Kelly fraction (using $4000 bet size)
            kelly_fraction = calculate_kelly_fraction(trades_for_kelly, bet_size=4000.0)
            if kelly_fraction is not None:
                kelly_fractions_json[key] = kelly_fraction
            else:
                kelly_fractions_json[key] = None
        
        # Prepare data for all dollar_amount values (if dollar_amount exists)
        # Use the SAME threshold/margin grid structure for all dollar amounts to ensure alignment
        all_data_by_dollar_amount = {}
        if has_dollar_amount:
            # Get all unique thresholds and margins from the full dataset (for consistent grid)
            all_thresholds = sorted(df['threshold'].unique().tolist())
            all_margins = sorted(df['margin'].unique().tolist())
            
            for dollar_amt in dollar_amount_values:
                df_dollar = df[df['dollar_amount'] == dollar_amt]
                
                # Create pivot with consistent index/columns (reindex to match all_thresholds/all_margins)
                pivot_dollar = df_dollar.pivot_table(
                    values=metric,
                    index='threshold',
                    columns='margin',
                    aggfunc='first'
                )
                
                # Reindex to ensure all threshold/margin combinations exist (fill with NaN if missing)
                pivot_dollar = pivot_dollar.reindex(index=all_thresholds, columns=all_margins)
                
                # Convert to list of lists for JavaScript
                all_data_by_dollar_amount[str(int(dollar_amt))] = {
                    'values': pivot_dollar.values.tolist(),
                    'thresholds': pivot_dollar.index.values.tolist(),
                    'margins': pivot_dollar.columns.values.tolist()
                }
        
        # Build JavaScript with dollar_amount support
        dollar_amount_js = ""
        if has_dollar_amount:
            dollar_amount_js = f"""
    // Dollar amount data
    const dollarAmountValues = {json.dumps([int(da) for da in dollar_amount_values])};
    const allDataByDollarAmount = {json.dumps(all_data_by_dollar_amount)};
    let currentDollarAmount = {int(default_dollar_amount)};
    
    // Update heatmap when dollar amount slider changes
    function updateHeatmapForDollarAmount(dollarAmount) {{
        const dollarAmountStr = dollarAmount.toString();
        const data = allDataByDollarAmount[dollarAmountStr];
        
        if (!data) {{
            console.log('No data for dollar amount:', dollarAmount);
            return;
        }}
        
        const gd = document.getElementById('plotly-div');
        if (!gd) return;
        
        // Format cell text to match hover format
        let cellText;
        if (data.text) {{
            // Use pre-formatted text if available
            cellText = [data.text];
        }} else {{
            // Format on the fly to match hover format
            if ('{metric}' === 'avg_roi' || '{metric}' === 'kelly_roi') {{
                cellText = [data.values.map(row => row.map(val => val !== null && !isNaN(val) ? (val * 100).toFixed(2) + '%' : ''))];
            }} else if ('{metric}' === 'win_rate') {{
                cellText = [data.values.map(row => row.map(val => val !== null && !isNaN(val) ? (val * 100).toFixed(1) + '%' : ''))];
            }} else {{
                cellText = [data.values.map(row => row.map(val => val !== null && !isNaN(val) ? val.toFixed(2) : ''))];
            }}
        }}
        
        // Update heatmap data (first trace, index 0)
        Plotly.restyle(gd, {{
            z: [data.values],
            x: [data.margins.map(m => m.toFixed(3))],
            y: [data.thresholds.map(t => t.toFixed(3))],
            text: cellText
        }}, {{}}, [0]);
        
        // Update hover texts
        const hoverTexts = [];
        for (let i = 0; i < data.values.length; i++) {{
            const hoverRow = [];
            for (let j = 0; j < data.values[i].length; j++) {{
                const val = data.values[i][j];
                if (val !== null && !isNaN(val)) {{
                    if ('{metric}' === 'avg_roi') {{
                        hoverRow.push(`Threshold: ${{data.thresholds[i].toFixed(3)}}<br>Margin: ${{data.margins[j].toFixed(3)}}<br>Dollar Amount: ${{dollarAmount}}<br>Avg ROI: ${{(val * 100).toFixed(2)}}%`);
                    }} else if ('{metric}' === 'win_rate') {{
                        hoverRow.push(`Threshold: ${{data.thresholds[i].toFixed(3)}}<br>Margin: ${{data.margins[j].toFixed(3)}}<br>Dollar Amount: ${{dollarAmount}}<br>Win Rate: ${{(val * 100).toFixed(1)}}%`);
                    }} else {{
                        hoverRow.push(`Threshold: ${{data.thresholds[i].toFixed(3)}}<br>Margin: ${{data.margins[j].toFixed(3)}}<br>Dollar Amount: ${{dollarAmount}}<br>Value: ${{val.toFixed(2)}}`);
                    }}
                }} else {{
                    hoverRow.push("");
                }}
            }}
            hoverTexts.push(hoverRow);
        }}
        
        Plotly.restyle(gd, {{
            hovertext: [hoverTexts]
        }}, {{}}, [0]);
        
        currentDollarAmount = dollarAmount;
    }}
    
    // Set up slider event listener
    document.addEventListener('DOMContentLoaded', function() {{
        const slider = document.getElementById('dollarAmountSlider');
        const valueDisplay = document.getElementById('dollarAmountValue');
        
        if (slider && valueDisplay) {{
            slider.addEventListener('input', function() {{
                const index = parseInt(this.value);
                const dollarAmount = dollarAmountValues[index];
                valueDisplay.textContent = '$' + dollarAmount;
                updateHeatmapForDollarAmount(dollarAmount);
            }});
        }}
    }});
"""
        
        js_callback = f"""
    <script>
    // Individual trades data
    const individualTrades = {json.dumps(individual_trades_json)};
    const kellyFractions = {json.dumps(kelly_fractions_json)};
    const originalThresholds = {json.dumps(thresholds_list)};
    const originalMargins = {json.dumps(margins_list)};
    let currentDollarAmountForHistogram = {int(default_dollar_amount) if has_dollar_amount else 'null'};
{dollar_amount_js}
    
    // Find closest threshold and margin values
    function findClosestValue(value, array) {{
        let minDiff = Infinity;
        let closestVal = array[0];
        for (let i = 0; i < array.length; i++) {{
            const diff = Math.abs(array[i] - value);
            if (diff < minDiff) {{
                minDiff = diff;
                closestVal = array[i];
            }}
        }}
        return closestVal;
    }}
    
    // Update histogram on click
    document.addEventListener('DOMContentLoaded', function() {{
        const gd = document.getElementById('plotly-div');
        if (!gd || !gd.data) return;
        
        // Update current dollar amount when slider changes
        const slider = document.getElementById('dollarAmountSlider');
        if (slider && typeof dollarAmountValues !== 'undefined' && dollarAmountValues) {{
            slider.addEventListener('input', function() {{
                const index = parseInt(this.value);
                if (dollarAmountValues[index] !== undefined) {{
                    currentDollarAmountForHistogram = dollarAmountValues[index];
                    console.log('Updated currentDollarAmountForHistogram to:', currentDollarAmountForHistogram);
                }}
            }});
        }}
        
        gd.on('plotly_click', function(data) {{
            if (!data || !data.points || data.points.length === 0) return;
            
            const point = data.points[0];
            if (point.curveNumber !== 0) return; // Only handle clicks on heatmap (first trace)
            
            // Parse threshold and margin from labels
            const thresholdLabel = point.y;
            const marginLabel = point.x;
            
            const threshold = parseFloat(thresholdLabel);
            const margin = parseFloat(marginLabel);
            
            if (isNaN(threshold) || isNaN(margin)) {{
                console.log('Could not parse threshold or margin');
                return;
            }}
            
            // Find closest match in individual trades
            const thresholdKey = findClosestValue(threshold, originalThresholds);
            const marginKey = findClosestValue(margin, originalMargins);
            
            // Build key - check if we need to include dollar_amount
            let key;
            // Use dollarAmountValues from dollar_amount_js if available
            if (currentDollarAmountForHistogram !== null && typeof dollarAmountValues !== 'undefined' && dollarAmountValues && dollarAmountValues.length > 0) {{
                // Use 3-part key: threshold,margin,dollar_amount
                const dollarKey = findClosestValue(currentDollarAmountForHistogram, dollarAmountValues);
                key = thresholdKey.toFixed(6) + ',' + marginKey.toFixed(6) + ',' + dollarKey.toFixed(0);
                console.log('Looking for 3-part key:', key, 'currentDollarAmountForHistogram:', currentDollarAmountForHistogram, 'dollarKey:', dollarKey);
            }} else {{
                // Use 2-part key: threshold,margin (for backward compatibility)
                key = thresholdKey.toFixed(6) + ',' + marginKey.toFixed(6);
                console.log('Looking for 2-part key:', key, '(dollar_amount not available, currentDollarAmountForHistogram:', currentDollarAmountForHistogram, ')');
            }}
            
            const roiValues = individualTrades[key];
            
            if (!roiValues || roiValues.length === 0) {{
                console.log('No data for key=' + key + ' (threshold=' + threshold + ', margin=' + margin + ', dollar=' + currentDollarAmountForHistogram + ')');
                console.log('Available keys sample:', Object.keys(individualTrades).slice(0, 5));
                // Try to find a close match
                const allKeys = Object.keys(individualTrades);
                for (let i = 0; i < allKeys.length; i++) {{
                    const parts = allKeys[i].split(',');
                    if (parts.length >= 2) {{
                        const keyThresh = parseFloat(parts[0]);
                        const keyMarg = parseFloat(parts[1]);
                        if (Math.abs(keyThresh - thresholdKey) < 0.0001 && Math.abs(keyMarg - marginKey) < 0.0001) {{
                            console.log('Found close match:', allKeys[i]);
                            const closeMatchValues = individualTrades[allKeys[i]];
                            if (closeMatchValues && closeMatchValues.length > 0) {{
                                // Use this match
                                Plotly.restyle(gd, {{
                                    x: [closeMatchValues],
                                    nbinsx: 50
                                }}, {{
                                    'xaxis2.title': 'ROI',
                                    'yaxis2.title': 'Count',
                                    'xaxis2.range': [Math.min(...closeMatchValues) * 1.1, Math.max(...closeMatchValues) * 1.1]
                                }}, [1]);
                                
                                let titleText = 'ROI Distribution<br><sub>Threshold: ' + threshold.toFixed(3) + ', Margin: ' + margin.toFixed(3);
                                if (currentDollarAmountForHistogram !== null) {{
                                    titleText += ', Dollar: $' + currentDollarAmountForHistogram;
                                }}
                                titleText += '<br>N=' + closeMatchValues.length;
                                // Add Kelly fraction if available
                                const kellyKey = thresholdKey.toFixed(6) + ',' + marginKey.toFixed(6) + (currentDollarAmountForHistogram !== null ? ',' + findClosestValue(currentDollarAmountForHistogram, dollarAmountValues).toFixed(0) : '');
                                if (kellyFractions[kellyKey] !== undefined && kellyFractions[kellyKey] !== null) {{
                                    titleText += '<br>Kelly Fraction: ' + (kellyFractions[kellyKey] * 100).toFixed(2) + '%';
                                }}
                                titleText += '</sub>';
                                Plotly.relayout(gd, {{
                                    'annotations[1].text': titleText
                                }});
                                return;
                            }}
                        }}
                    }}
                }}
                // Clear histogram
                Plotly.restyle(gd, {{x: [[]]}}, {{}}, [1]);
                return;
            }}
            
            // Update histogram (second trace, index 1)
            Plotly.restyle(gd, {{
                x: [roiValues],
                nbinsx: 50
            }}, {{
                'xaxis2.title': 'ROI',
                'yaxis2.title': 'Count',
                'xaxis2.range': [Math.min(...roiValues) * 1.1, Math.max(...roiValues) * 1.1]
            }}, [1]);
            
            // Update subplot title
            let titleText = 'ROI Distribution<br><sub>Threshold: ' + threshold.toFixed(3) + ', Margin: ' + margin.toFixed(3);
            if (currentDollarAmountForHistogram !== null) {{
                titleText += ', Dollar: $' + currentDollarAmountForHistogram;
            }}
            titleText += '<br>N=' + roiValues.length;
            // Add Kelly fraction if available
            if (kellyFractions[key] !== undefined && kellyFractions[key] !== null) {{
                titleText += '<br>Kelly Fraction: ' + (kellyFractions[key] * 100).toFixed(2) + '%';
            }}
            titleText += '</sub>';
            Plotly.relayout(gd, {{
                'annotations[1].text': titleText
            }});
        }});
    }});
    </script>
    """
        
        # Inject JavaScript before closing body tag
        html_content = html_content.replace('</body>', js_callback + '\n</body>')
        
        # Write the modified HTML
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        return filepath
    
    return None
    
    # Save as HTML
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        filename = f'{market_type}_{metric}_heatmap.html'
        filepath = os.path.join(output_dir, filename)
        fig.write_html(filepath)
        return filepath
    
    return None


def create_kelly_roi_heatmap(
    df: pd.DataFrame,
    individual_trades: Dict,
    market_type: str,
    metric: str = "kelly_roi",
    output_dir: str = None
) -> Optional[str]:
    """
    Create a heatmap showing ROI if betting at Kelly optimal sizing.
    
    Args:
        df: DataFrame with grid search results
        individual_trades: Dict mapping (threshold, margin, dollar_amount) -> list of trade results
        market_type: '15m' or '1h'
        metric: Metric name for the heatmap
        output_dir: Output directory for saving files
        
    Returns:
        Path to HTML file if created, None otherwise
    """
    if not PLOTLY_AVAILABLE:
        return None
    
    if df.empty:
        return None
    
    if not individual_trades or len(individual_trades) == 0:
        return None
    
    # Check if dollar_amount column exists
    has_dollar_amount = 'dollar_amount' in df.columns
    
    if has_dollar_amount:
        dollar_amount_values = sorted(df['dollar_amount'].unique().tolist())
        default_dollar_amount = dollar_amount_values[0]
        df_filtered = df[df['dollar_amount'] == default_dollar_amount].copy()
    else:
        dollar_amount_values = []
        default_dollar_amount = None
        df_filtered = df.copy()
    
    # Calculate Kelly ROI for each parameter combination
    kelly_roi_dict = {}
    
    # Process each parameter combination in individual_trades
    for key_tuple, trade_data in individual_trades.items():
        if not trade_data:
            continue
        
        # Determine if we have full trade objects or just ROI values
        if isinstance(trade_data, list) and len(trade_data) > 0:
            if isinstance(trade_data[0], dict):
                trades_for_kelly = trade_data
            else:
                # ROI values only - create minimal trade dicts
                trades_for_kelly = [{'roi': float(roi), 'is_win': float(roi) > 0, 'dollar_amount': 4000.0} for roi in trade_data]
        else:
            continue
        
        # Calculate Kelly ROI
        kelly_roi = calculate_kelly_roi(trades_for_kelly, bet_size=4000.0)
        
        if kelly_roi is not None:
            kelly_roi_dict[key_tuple] = kelly_roi
    
    if not kelly_roi_dict:
        return None
    
    # Create DataFrame with Kelly ROI values
    kelly_results = []
    for key_tuple, kelly_roi in kelly_roi_dict.items():
        if len(key_tuple) == 3:
            threshold, margin, dollar_amount = key_tuple
            if has_dollar_amount and abs(dollar_amount - default_dollar_amount) < 0.01:
                kelly_results.append({
                    'threshold': threshold,
                    'margin': margin,
                    'dollar_amount': dollar_amount,
                    'kelly_roi': kelly_roi
                })
        elif len(key_tuple) == 2:
            threshold, margin = key_tuple
            kelly_results.append({
                'threshold': threshold,
                'margin': margin,
                'kelly_roi': kelly_roi
            })
    
    if not kelly_results:
        return None
    
    kelly_df = pd.DataFrame(kelly_results)
    
    # Merge with original df to preserve structure
    df_with_kelly = df_filtered.copy()
    df_with_kelly['kelly_roi'] = 0.0
    
    # Merge Kelly ROI values
    for result in kelly_results:
        mask = (df_with_kelly['threshold'] == result['threshold']) & \
               (df_with_kelly['margin'] == result['margin'])
        if has_dollar_amount and 'dollar_amount' in result:
            mask = mask & (df_with_kelly['dollar_amount'] == result['dollar_amount'])
        if mask.any():
            df_with_kelly.loc[mask, 'kelly_roi'] = result['kelly_roi']
    
    # Create empty individual_trades dict for Kelly ROI (we don't need it for this visualization)
    df_with_kelly.attrs = getattr(df_with_kelly, 'attrs', {})
    df_with_kelly.attrs['individual_trades'] = {}
    
    # Create heatmap using existing function
    return create_interactive_heatmap(
        df_with_kelly,
        metric='kelly_roi',
        market_type=market_type,
        output_dir=output_dir
    )


def display_grid(df: pd.DataFrame, metric: str = "avg_roi", title: str = ""):
    """Display results as a grid: threshold (rows) vs margin (columns)."""
    if df.empty:
        print(f"No data for {title}")
        return
    
    # Create pivot table
    pivot = df.pivot_table(
        values=metric,
        index='threshold',
        columns='margin',
        aggfunc='first'
    )
    
    print(f"\n{'='*80}")
    print(f"{title} - {metric.upper()} GRID")
    print(f"{'='*80}")
    print(f"\nRows = Threshold, Columns = Margin")
    print()
    
    # Format the pivot table for display
    formatted_pivot = pivot.copy()
    
    # Round values for display
    if metric in ['avg_roi', 'total_roi']:
        formatted_pivot = formatted_pivot.round(4)
    elif metric in ['win_rate']:
        formatted_pivot = formatted_pivot.round(3)
    else:
        formatted_pivot = formatted_pivot.round(2)
    
    # Display with better formatting
    print(formatted_pivot.to_string())
    print()


def display_summary_stats(df: pd.DataFrame, market_type: str, num_markets: int = None):
    """Display summary statistics for the results."""
    if df.empty:
        print(f"No results for {market_type} markets")
        return
    
    print(f"\n{'='*80}")
    print(f"{market_type.upper()} MARKETS - SUMMARY STATISTICS")
    print(f"{'='*80}")
    if num_markets is not None:
        print(f"Markets analyzed: {num_markets}")
    print(f"Total parameter combinations tested: {len(df)}")
    print(f"Combinations with trades: {(df['num_trades'] > 0).sum()}")
    print(f"Combinations with win rate > 50%: {(df['win_rate'] > 0.5).sum()}")
    print(f"Combinations with positive avg ROI: {(df['avg_roi'] > 0).sum()}")
    print()
    
    if (df['num_trades'] > 0).sum() > 0:
        df_with_trades = df[df['num_trades'] > 0]
        print(f"Best win rate: {df_with_trades['win_rate'].max():.4f}")
        print(f"Best avg ROI: {df_with_trades['avg_roi'].max():.4f}")
        print(f"Total trades across all combinations: {df_with_trades['num_trades'].sum()}")
        print()
        
        # Show top 10 combinations by avg ROI
        print("TOP 10 COMBINATIONS BY AVG ROI:")
        top_roi = df_with_trades.nlargest(10, 'avg_roi')[
            ['threshold', 'margin', 'num_trades', 'win_rate', 'avg_roi']
        ]
        print(top_roi.to_string(index=False))
        print()


def run_grid_search_for_market_type(
    market_type: str,
    threshold_min: float = 0.60,
    threshold_max: float = 1.00,
    threshold_step: float = 0.05,  # Larger step for faster testing
    margin_min: float = 0.01,
    margin_step: float = 0.02,  # Larger step for faster testing
    min_dollar_amount: float = 1.0,
    max_dollar_amount: float = 1000.0,
    dollar_amount_interval: float = 50.0,
    max_minutes_until_resolution: float = None,
    max_markets: int = None,
    start_date: datetime = None,
    end_date: datetime = None,
    output_dir: str = None
):
    """Run grid search for a specific market type (15m or 1h)."""
    print(f"\n{'='*80}")
    print(f"RUNNING GRID SEARCH FOR {market_type.upper()} MARKETS")
    print(f"{'='*80}")
    
    # Initialize backtester for specific market type
    if market_type == "15m":
        backtester = ThresholdBacktester(use_15m_table=True, use_1h_table=False)
    elif market_type == "1h":
        backtester = ThresholdBacktester(use_15m_table=False, use_1h_table=True)
    else:
        raise ValueError(f"Invalid market_type: {market_type}. Must be '15m' or '1h'")
    
    # Get markets
    print(f"\nLoading {market_type} markets...")
    markets = backtester.get_markets_with_orderbooks(
        start_date=start_date,
        end_date=end_date,
        max_markets=max_markets
    )
    num_markets = len(markets)
    print(f"Found {num_markets} {market_type} markets")
    
    if num_markets == 0:
        print(f"No {market_type} markets found. Skipping grid search.")
        return pd.DataFrame(), []
    
    # Run grid search
    print(f"\nRunning grid search on {num_markets} {market_type} markets...")
    print(f"Threshold: {threshold_min:.2f} to {threshold_max:.2f} (step {threshold_step:.2f})")
    print(f"Margin: {margin_min:.2f} to auto (step {margin_step:.2f})")
    print(f"Dollar amount: ${min_dollar_amount:.0f} to ${max_dollar_amount:.0f} (step ${dollar_amount_interval:.0f})")
    
    results_df = backtester.run_grid_search(
        markets=markets,
        threshold_min=threshold_min,
        threshold_max=threshold_max,
        threshold_step=threshold_step,
        margin_min=margin_min,
        margin_step=margin_step,
        min_dollar_amount=min_dollar_amount,
        max_dollar_amount=max_dollar_amount,
        dollar_amount_interval=dollar_amount_interval,
        max_minutes_until_resolution=max_minutes_until_resolution,
        return_individual_trades=True  # Return individual trade ROI values for histogram
    )
    
    if results_df.empty:
        print(f"No results found for {market_type} markets!")
        return pd.DataFrame(), []
    
    # Display grids for different metrics
    display_grid(results_df, "avg_roi", f"{market_type.upper()} Markets")
    display_grid(results_df, "win_rate", f"{market_type.upper()} Markets")
    
    # Create and save heatmaps if plotting is available
    plot_files = []
    html_files = []
    if PLOTTING_AVAILABLE:
        print(f"\nGenerating heatmap visualizations...")
        metrics_to_visualize = ["avg_roi", "win_rate"]
        
        # Add Kelly ROI if available in results
        if "kelly_roi" in results_df.columns:
            metrics_to_visualize.append("kelly_roi")
        
        for metric in metrics_to_visualize:
            result = create_heatmap(results_df, metric, market_type, output_dir=output_dir)
            if result:
                if isinstance(result, tuple):
                    # Returns both PNG and HTML
                    png_file, html_file = result
                    plot_files.append(png_file)
                    html_files.append(html_file)
                    print(f"  ✓ Created {png_file}")
                    print(f"  ✓ Created interactive {html_file}")
                else:
                    # Just PNG
                    plot_files.append(result)
                    print(f"  ✓ Created {result}")
    
    # Display summary statistics
    display_summary_stats(results_df, market_type, num_markets=num_markets)
    
    return results_df, (plot_files, html_files)


def main():
    parser = argparse.ArgumentParser(description="Test threshold strategy grid search for 15m and 1h markets")
    parser.add_argument("--threshold-min", type=float, default=0.60, help="Minimum threshold (default: 0.60)")
    parser.add_argument("--threshold-max", type=float, default=1.00, help="Maximum threshold (default: 1.00)")
    parser.add_argument("--threshold-step", type=float, default=0.05, help="Threshold step (default: 0.05 for faster testing)")
    parser.add_argument("--margin-min", type=float, default=0.01, help="Minimum margin (default: 0.01)")
    parser.add_argument("--margin-step", type=float, default=0.02, help="Margin step (default: 0.02 for faster testing)")
    parser.add_argument("--min-dollar-amount", type=float, default=1.0, help="Minimum dollar amount (default: 1)")
    parser.add_argument("--max-dollar-amount", type=float, default=1000.0, help="Maximum dollar amount (default: 1000)")
    parser.add_argument("--dollar-amount-interval", type=float, default=50.0, help="Dollar amount interval (default: 50)")
    parser.add_argument("--max-minutes-until-resolution", type=float, default=None, help="Only trigger trades if <= X minutes until resolution (default: None = no filter)")
    parser.add_argument("--max-markets", type=int, default=None, help="Maximum number of markets to test per type")
    parser.add_argument("--start-date", type=str, default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument("--15m-only", action="store_true", dest="only_15m", help="Only test 15-minute markets")
    parser.add_argument("--1h-only", action="store_true", dest="only_1h", help="Only test 1-hour markets")
    parser.add_argument("--output-dir", type=str, default=None, help="Directory to save CSV results")
    
    args = parser.parse_args()
    
    # Parse dates
    start_date = None
    end_date = None
    if args.start_date:
        start_date = datetime.fromisoformat(args.start_date).replace(tzinfo=timezone.utc)
    if args.end_date:
        end_date = datetime.fromisoformat(args.end_date).replace(tzinfo=timezone.utc)
    
    print("=" * 80)
    print("THRESHOLD STRATEGY GRID SEARCH - 15M AND 1H MARKETS")
    print("=" * 80)
    print()
    print(f"Threshold range: {args.threshold_min:.2f} to {args.threshold_max:.2f} (step {args.threshold_step:.2f})")
    print(f"Margin range: {args.margin_min:.2f} to auto (step {args.margin_step:.2f})")
    print(f"Dollar amount range: ${args.min_dollar_amount:.0f} to ${args.max_dollar_amount:.0f} (step ${args.dollar_amount_interval:.0f})")
    if args.max_minutes_until_resolution:
        print(f"Time filter: Only trade if <= {args.max_minutes_until_resolution:.1f} minutes until resolution")
    print(f"Max markets per type: {args.max_markets or 'all'}")
    print()
    
    results_15m = None
    results_1h = None
    plot_files_15m = []
    plot_files_1h = []
    html_files_15m = []
    html_files_1h = []
    
    # Run grid search for 15-minute markets
    if not args.only_1h:
        results_15m, (plot_files_15m, html_files_15m) = run_grid_search_for_market_type(
            "15m",
            threshold_min=args.threshold_min,
            threshold_max=args.threshold_max,
            threshold_step=args.threshold_step,
            margin_min=args.margin_min,
            margin_step=args.margin_step,
            min_dollar_amount=args.min_dollar_amount,
            max_dollar_amount=args.max_dollar_amount,
            dollar_amount_interval=args.dollar_amount_interval,
            max_minutes_until_resolution=args.max_minutes_until_resolution,
            max_markets=args.max_markets,
            start_date=start_date,
            end_date=end_date,
            output_dir=args.output_dir
        )
        
        if args.output_dir:
            os.makedirs(args.output_dir, exist_ok=True)
            if not results_15m.empty:
                output_file = os.path.join(args.output_dir, "threshold_grid_15m.csv")
                results_15m.to_csv(output_file, index=False)
                print(f"\n✓ Saved 15m CSV results to {output_file}")
            
            # Save heatmaps if available
            if plot_files_15m:
                print(f"\n✓ Saved {len(plot_files_15m)} 15m heatmap plots")
    
    # Run grid search for 1-hour markets
    if not args.only_15m:
        results_1h, (plot_files_1h, html_files_1h) = run_grid_search_for_market_type(
            "1h",
            threshold_min=args.threshold_min,
            threshold_max=args.threshold_max,
            threshold_step=args.threshold_step,
            margin_min=args.margin_min,
            margin_step=args.margin_step,
            min_dollar_amount=args.min_dollar_amount,
            max_dollar_amount=args.max_dollar_amount,
            dollar_amount_interval=args.dollar_amount_interval,
            max_minutes_until_resolution=args.max_minutes_until_resolution,
            max_markets=args.max_markets,
            start_date=start_date,
            end_date=end_date,
            output_dir=args.output_dir
        )
        
        if args.output_dir:
            os.makedirs(args.output_dir, exist_ok=True)
            if not results_1h.empty:
                output_file = os.path.join(args.output_dir, "threshold_grid_1h.csv")
                results_1h.to_csv(output_file, index=False)
                print(f"\n✓ Saved 1h CSV results to {output_file}")
            
            # Save heatmaps if available
            if plot_files_1h:
                print(f"\n✓ Saved {len(plot_files_1h)} 1h PNG heatmap plots")
            if html_files_1h:
                print(f"✓ Saved {len(html_files_1h)} 1h interactive HTML heatmaps")
    
    print("\n" + "=" * 80)
    print("GRID SEARCH COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()

