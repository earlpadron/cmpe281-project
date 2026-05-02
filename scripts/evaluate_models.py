import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    root_mean_squared_error,
)

# Must match default routing budget in backend/main.py when env is unset.
USER_BUDGET_USD = float(os.environ.get("USER_BUDGET_USD", "0.000050"))


def route_cloud(edge_ms: float, cloud_ms: float, cost_usd: float) -> int:
    """1 = CLOUD, 0 = EDGE (same rule as production placement)."""
    if (cloud_ms < edge_ms) and (cost_usd <= USER_BUDGET_USD):
        return 1
    return 0


def save_evaluation_plots(
    plots_dir: Path,
    y_edge_true,
    y_edge_pred,
    y_cloud_true,
    y_cloud_pred,
    y_cost_true,
    y_cost_pred,
    actual_route: np.ndarray,
    pred_route: np.ndarray,
) -> None:
    """Regression diagnostics + routing agreement (classification-style metrics)."""
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print(
            "matplotlib not installed. Install with: "
            "pip install -r scripts/requirements-train.txt"
        )
        return

    plots_dir.mkdir(parents=True, exist_ok=True)

    def scatter(y_true, y_pred, title: str, xlabel: str, ylabel: str, fname: str) -> None:
        fig, ax = plt.subplots(figsize=(5, 5))
        ax.scatter(y_true, y_pred, alpha=0.35, s=12, edgecolors="none")
        lo = float(min(np.min(y_true), np.min(y_pred)))
        hi = float(max(np.max(y_true), np.max(y_pred)))
        ax.plot([lo, hi], [lo, hi], "r--", lw=1, label="perfect")
        ax.set_title(title)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.legend(loc="upper left", fontsize=8)
        ax.set_aspect("equal", adjustable="box")
        fig.tight_layout()
        fig.savefig(plots_dir / fname, dpi=150)
        plt.close(fig)

    scatter(
        y_edge_true,
        y_edge_pred,
        "Edge latency (test)",
        "Actual (ms)",
        "Predicted (ms)",
        "edge_latency_pred_vs_actual.png",
    )
    scatter(
        y_cloud_true,
        y_cloud_pred,
        "Cloud latency (test)",
        "Actual (ms)",
        "Predicted (ms)",
        "cloud_latency_pred_vs_actual.png",
    )
    scatter(
        y_cost_true,
        y_cost_pred,
        "Cloud cost (test)",
        "Actual (USD)",
        "Predicted (USD)",
        "cloud_cost_pred_vs_actual.png",
    )

    labels = ("EDGE", "CLOUD")
    cm = confusion_matrix(actual_route, pred_route, labels=[0, 1])
    fig, ax = plt.subplots(figsize=(4.5, 4))
    im = ax.imshow(cm, interpolation="nearest", cmap=plt.cm.Blues)
    ax.figure.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    ax.set(
        xticks=np.arange(cm.shape[1]),
        yticks=np.arange(cm.shape[0]),
        xticklabels=labels,
        yticklabels=labels,
    )
    ax.set_ylabel("Actual route (oracle)")
    ax.set_xlabel("Predicted route (ML latencies + budget)")
    ax.set_title("Routing agreement (test split)")
    thresh = cm.max() / 2.0 if cm.size else 0
    for i in range(cm.shape[0]):
        for j in range(cm.shape[1]):
            color = "white" if cm[i, j] > thresh else "black"
            ax.text(j, i, format(cm[i, j], "d"), ha="center", va="center", color=color)
    fig.tight_layout()
    fig.savefig(plots_dir / "routing_confusion_matrix.png", dpi=150)
    plt.close(fig)


def evaluate_models(csv_path: str) -> None:
    print(f"Loading empirical dataset from: {csv_path}")
    df = pd.read_csv(csv_path)

    edge_features = ["image_size_bytes", "edge_cpu_utilization", "edge_memory_utilization"]
    cloud_features = ["image_size_bytes", "network_rtt_ms", "estimated_uplink_bandwidth_kbps"]

    X_edge = df[edge_features]
    y_edge_latency = df["edge_total_latency_ms"]
    X_cloud = df[cloud_features]
    y_cloud_latency = df["cloud_total_latency_ms"]
    y_cloud_cost = df["estimated_cloud_cost_usd"]

    split_idx = int(len(df) * 0.8)
    X_edge_test = X_edge.iloc[split_idx:]
    y_edge_lat_test = y_edge_latency.iloc[split_idx:]
    X_cloud_test = X_cloud.iloc[split_idx:]
    y_cloud_lat_test = y_cloud_latency.iloc[split_idx:]
    y_cloud_cost_test = y_cloud_cost.iloc[split_idx:]

    repo_root = Path(__file__).resolve().parent.parent
    models_dir = repo_root / "backend" / "models"
    edge_model = joblib.load(models_dir / "edge_latency_model.pkl")
    cloud_lat_model = joblib.load(models_dir / "cloud_latency_model.pkl")
    cloud_cost_model = joblib.load(models_dir / "cloud_cost_model.pkl")

    edge_preds = edge_model.predict(X_edge_test)
    cloud_preds = cloud_lat_model.predict(X_cloud_test)
    cost_preds = cloud_cost_model.predict(X_cloud_test)

    print("\n--- Loaded model evaluation on test split ---")
    print(f"Edge Latency MAE: {mean_absolute_error(y_edge_lat_test, edge_preds):.2f} ms")
    print(f"Edge Latency RMSE: {root_mean_squared_error(y_edge_lat_test, edge_preds):.2f} ms")
    print(f"Cloud Latency MAE: {mean_absolute_error(y_cloud_lat_test, cloud_preds):.2f} ms")
    print(f"Cloud Latency RMSE: {root_mean_squared_error(y_cloud_lat_test, cloud_preds):.2f} ms")
    print(f"Cloud Cost MAE: ${mean_absolute_error(y_cloud_cost_test, cost_preds):.6f}")

    y_e = y_edge_lat_test.to_numpy()
    y_c = y_cloud_lat_test.to_numpy()
    y_cost = y_cloud_cost_test.to_numpy()
    actual_route = np.array([route_cloud(y_e[i], y_c[i], y_cost[i]) for i in range(len(y_e))])
    pred_route = np.array(
        [route_cloud(edge_preds[i], cloud_preds[i], cost_preds[i]) for i in range(len(edge_preds))]
    )

    f1 = f1_score(actual_route, pred_route, pos_label=1, zero_division=0)
    labels = ("EDGE", "CLOUD")
    print("\n--- Routing agreement (CLOUD=positive class) ---")
    print(f"F1 score (CLOUD): {f1:.4f}")
    print(
        classification_report(
            actual_route,
            pred_route,
            labels=[0, 1],
            target_names=labels,
            zero_division=0,
        )
    )

    plots_dir = repo_root / "plots"
    save_evaluation_plots(
        plots_dir,
        y_e,
        edge_preds,
        y_c,
        cloud_preds,
        y_cost,
        cost_preds,
        actual_route,
        pred_route,
    )
    print(f"Plots saved under: {plots_dir.resolve()}")


if __name__ == "__main__":
    LATEST_CSV = "benchmark_results_20260416_024523.csv"
    evaluate_models(LATEST_CSV)
