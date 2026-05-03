import argparse
import pandas as pd


CLOUD_COST_PER_REQUEST_USD = 0.000004


def summarize(df, mode):
    df = df[df["success"] == True].copy()

    total_requests = len(df)
    cloud_requests = (df["route"] == "CLOUD").sum()
    edge_requests = (df["route"] == "EDGE").sum()

    return {
        "mode": mode,
        "requests": total_requests,
        "avg_latency_ms": df["latency_ms"].mean(),
        "median_latency_ms": df["latency_ms"].median(),
        "p95_latency_ms": df["latency_ms"].quantile(0.95),
        "edge_requests": edge_requests,
        "cloud_requests": cloud_requests,
        "estimated_cost_usd": cloud_requests * CLOUD_COST_PER_REQUEST_USD,
    }


def main(edge_csv, cloud_csv, ml_csv):
    edge_df = pd.read_csv(edge_csv)
    cloud_df = pd.read_csv(cloud_csv)
    ml_df = pd.read_csv(ml_csv)

    summary = pd.DataFrame([
        summarize(edge_df, "EDGE only"),
        summarize(cloud_df, "CLOUD only"),
        summarize(ml_df, "ML routing"),
    ])

    print("\n--- Load Test Summary ---")
    print(summary.to_string(index=False))

    cloud_avg = summary.loc[summary["mode"] == "CLOUD only", "avg_latency_ms"].iloc[0]
    edge_avg = summary.loc[summary["mode"] == "EDGE only", "avg_latency_ms"].iloc[0]
    ml_avg = summary.loc[summary["mode"] == "ML routing", "avg_latency_ms"].iloc[0]

    cloud_cost = summary.loc[summary["mode"] == "CLOUD only", "estimated_cost_usd"].iloc[0]
    ml_cost = summary.loc[summary["mode"] == "ML routing", "estimated_cost_usd"].iloc[0]

    print("\n--- Savings vs Baselines ---")
    print(f"Time saved vs CLOUD only: {cloud_avg - ml_avg:.2f} ms per request")
    print(f"Time saved vs EDGE only: {edge_avg - ml_avg:.2f} ms per request")
    print(f"Cost saved vs CLOUD only: ${cloud_cost - ml_cost:.6f}")

    summary.to_csv("results/load_test_summary.csv", index=False)
    print("\nSaved summary to results/load_test_summary.csv")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--edge", default="results/edge_results.csv")
    parser.add_argument("--cloud", default="results/cloud_results.csv")
    parser.add_argument("--ml", default="results/ml_results.csv")

    args = parser.parse_args()
    main(args.edge, args.cloud, args.ml)