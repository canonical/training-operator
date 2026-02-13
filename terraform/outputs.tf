output "app_name" {
  value = juju_application.kubeflow-trainer.name
}

output "provides" {
  value = {
    grafana_dashboard = "grafana-dashboard",
    metrics_endpoint  = "metrics-endpoint",
    provide_cmr_mesh  = "provide-cmr-mesh",
  }
}

output "requires" {
  value = {
    dashboard_links  = "dashboard-links",
    service_mesh     = "service-mesh",
    require_cmr_mesh = "require-cmr-mesh",
  }
}
