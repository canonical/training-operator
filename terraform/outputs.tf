output "app_name" {
  value = juju_application.kubeflow-trainer.name
}

output "provides" {
  value = {
    grafana_dashboard = "grafana-dashboard",
    metrics_endpoint  = "metrics-endpoint",
  }
}

output "requires" {
  value = {
    dashboard_links = "dashboard-links"
  }
}
