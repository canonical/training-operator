output "app_name" {
  value = juju_application.training_operator.name
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
