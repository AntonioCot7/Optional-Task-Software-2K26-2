"""Resume el último análisis de SonarQube de un proyecto (evidencia en texto).

Uso:
    SONAR_HOST_URL=http://localhost:9000 SONAR_TOKEN=<token de usuario> \
        uv run python scripts/sonar_resumen.py [clave_proyecto]
    (o SONAR_LOGIN y SONAR_PASSWORD en lugar del token)

Lista: versión del servidor, perfil de calidad, Quality Gate, métricas globales, cobertura por archivo
y todos los hallazgos abiertos. Solo lee; no modifica nada.
"""
import base64
import json
import os
import sys
import urllib.parse
import urllib.request

HOST = os.getenv("SONAR_HOST_URL", "http://localhost:9000").rstrip("/")
PROJECT = sys.argv[1] if len(sys.argv) > 1 else "tienda-lab"
METRICS = ("ncloc,coverage,line_coverage,lines_to_cover,uncovered_lines,bugs,vulnerabilities,code_smells,"
           "security_hotspots,duplicated_lines_density,complexity,cognitive_complexity,"
           "reliability_rating,security_rating,sqale_rating,sqale_index")


def call(path, **params):
    request = urllib.request.Request(f"{HOST}{path}?{urllib.parse.urlencode(params)}")
    token, login = os.getenv("SONAR_TOKEN"), os.getenv("SONAR_LOGIN")
    if token:
        credentials = f"{token}:"
    elif login:
        credentials = f"{login}:{os.getenv('SONAR_PASSWORD', '')}"
    else:
        sys.exit("Defina SONAR_TOKEN (o SONAR_LOGIN y SONAR_PASSWORD).")
    request.add_header("Authorization", "Basic " + base64.b64encode(credentials.encode()).decode())
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def main():
    status = call("/api/system/status")
    print(f"SonarQube {status['version']} ({HOST}) estado={status['status']}")
    profiles = call("/api/qualityprofiles/search", project=PROJECT)["profiles"]
    print("Perfiles de calidad:", ", ".join(f"{p['name']} [{p['language']}]" for p in profiles if p["language"] == "py"))
    gate = call("/api/qualitygates/project_status", projectKey=PROJECT)["projectStatus"]
    print(f"Quality Gate: {gate['status']}")
    for condition in gate["conditions"]:
        print(f"  - {condition['metricKey']}: {condition['status']} (valor {condition.get('actualValue')})")
    analyses = call("/api/project_analyses/search", project=PROJECT, ps=5)["analyses"]
    print("Análisis recientes:", ", ".join(f"{a.get('projectVersion', '?')}@{a['date'][:19]}" for a in analyses))
    print("\nMétricas globales (todo el código):")
    for measure in call("/api/measures/component", component=PROJECT, metricKeys=METRICS)["component"]["measures"]:
        print(f"  {measure['metric']:26} {measure.get('value')}")
    print("\nCobertura por archivo:")
    tree = call("/api/measures/component_tree", component=PROJECT, qualifiers="FIL", ps=100,
                metricKeys="coverage,uncovered_lines,lines_to_cover")["components"]
    for component in sorted(tree, key=lambda c: c["path"]):
        values = {m["metric"]: m.get("value") for m in component["measures"]}
        print(f"  {component['path']:30} cobertura={values.get('coverage', '-'):>6}  "
              f"líneas={values.get('lines_to_cover', '-'):>4}  sin cubrir={values.get('uncovered_lines', '-')}")
    issues = call("/api/issues/search", componentKeys=PROJECT, resolved="false", ps=500)
    print(f"\nHallazgos abiertos: {issues['total']}")
    for issue in issues["issues"]:
        print(f"  [{issue['severity']}] {issue['type']} {issue['rule']} "
              f"{issue['component'].split(':')[-1]}:{issue.get('line', '-')} - {issue['message']}")


if __name__ == "__main__":
    main()
