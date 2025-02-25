from flask import Flask, jsonify, request
import pika
import os
import json
from flask_cors import CORS
import sys
import threading
import time
from kubernetes import client, config

# Get the current script's directory
current_dir = os.path.dirname(os.path.abspath(__file__))
# Get the parent directory
parent_dir = os.path.dirname(current_dir)

print("Parent Directory:", parent_dir)
sys.path.append(parent_dir)

from redis_utils import RedisUtils
redis_utils = RedisUtils()

# Numero minimo de workers activos
MIN_WORKERS_COUNT = 5

# --- APP side --- 
app = Flask(__name__)
CORS(app)

# Función que actualiza las réplicas en el Deployment
def actualizar_replicas_en_kubernetes(deployment_name, namespace, replicas):

    # Después (esto funciona dentro del cluster de Kubernetes)
    config.load_incluster_config()
    
    # Crear el cliente de la API de Kubernetes
    v1_apps = client.AppsV1Api()

    # Obtener el Deployment
    deployment = v1_apps.read_namespaced_deployment(deployment_name, namespace)

    # Actualizar el número de réplicas
    deployment.spec.replicas = replicas

    # Aplicar el cambio
    v1_apps.replace_namespaced_deployment(deployment_name, namespace, deployment)
    print(f"El número de réplicas de {deployment_name} en el namespace {namespace} ha sido actualizado a {MIN_WORKERS_COUNT-replicas}.")

# Función principal que se ejecuta cada X segundos
def ejecutar_periodicamente(deployment_name, namespace, intervalo_segundos):
    while True:
        # Contamos workers en la nube y locales
        workers_cloud = redis_utils.get_active_workers(prefix="workers_cloud")
        workers_local = redis_utils.get_active_workers(prefix="workers_local")
        total_workers = workers_cloud + workers_local

        print(f"Workers en la nube: {workers_cloud}, locales: {workers_local}, total: {total_workers}")

        # Si hay menos del mínimo requerido, escalar
        if workers_local < MIN_WORKERS_COUNT:
            actualizar_replicas_en_kubernetes(deployment_name, namespace, MIN_WORKERS_COUNT-workers_local)

        print(f"⏳ Esperando {intervalo_segundos} segundos para la próxima verificación...")
        time.sleep(intervalo_segundos)

@app.route('/keep_alive', methods=['POST'])
def check_status():
    data = request.get_json()
    worker_id = data.get("worker_id")  
    is_in_cloud = data.get("worker_user") == "true"  # Convertir a booleano

    if not worker_id:
        return jsonify({'error': 'Missing worker_id'}), 400

    # Guardar o actualizar el worker con TTL de 30s
    redis_utils.setex(f"workers:{worker_id}", 15, "alive")  

    # Guardar la categoría (nube/local)
    if is_in_cloud:
        redis_utils.setex(f"workers_cloud:{worker_id}", 15, "alive")
    else:
        redis_utils.setex(f"workers_local:{worker_id}", 15, "alive")

    print(f"Worker {worker_id} registered. Cloud: {is_in_cloud}")
    return jsonify({'status': 'OK'})

def connect_rabbitmq():
    while True:
        try:
            connection = pika.BlockingConnection(pika.ConnectionParameters(host='service-rabbitmq.default.svc.cluster.local', port=5672, credentials=pika.PlainCredentials('guest', 'guest')))
            return connection
        except pika.exceptions.AMQPConnectionError:
            print("Fallo en la conexión, reintentando en 5 segundos...")
            time.sleep(5)

def on_message_received(ch, method, properties, body):
    data = json.loads(body)
    print(f"Message {data} received")

    # Obtener cantidad de workers activos
    num_workers = redis_utils.get_active_workers()
    num_workers = max(num_workers, 1)  # Evitar división por cero

    print(f"Active workers: {num_workers}")

    connection = connect_rabbitmq()
    channel = connection.channel()

    # Dividir el rango de números aleatorios según los workers disponibles
    max_value = data['random_num_max']
    step = max_value // num_workers

    for i in range(num_workers):
        task_data = {
            "id": data['id'],
            "transactions": data['transactions'],
            "prefix": data['prefix'],
            "base_string_chain": data['base_string_chain'],
            "blockchain_content": data['blockchain_content'],
            "random_start": i * step,
            "random_end": (i + 1) * step if i < num_workers - 1 else max_value
        }

        # Publicar la subtarea en RabbitMQ
        channel.basic_publish(
            exchange='workers_queue',
            routing_key='hash_task',
            body=json.dumps(task_data)
        )
        print(f"Subtask {i+1}/{num_workers} sent: {task_data['random_start']} - {task_data['random_end']}")

    ch.basic_ack(delivery_tag=method.delivery_tag)
    connection.close()

def run_flask():
    """Ejecuta Flask en un hilo separado."""
    app.run(host='0.0.0.0', port=8080, debug=True, use_reloader=False)

def run_rabbitmq():
    """Conecta a RabbitMQ y empieza a consumir mensajes."""
    connection = connect_rabbitmq()
    channel = connection.channel()
    channel.exchange_declare(exchange='workers_queue', exchange_type='topic', durable=True)
    channel.queue_declare(queue='challenge_queue', durable=True)
    channel.queue_bind(exchange='block_challenge', queue='challenge_queue', routing_key='blocks')
    channel.basic_consume(queue='challenge_queue', on_message_callback=on_message_received, auto_ack=False)
    
    print('Waiting for messages. To exit press CTRL+C')
    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        print("Consumption stopped by user.")
        connection.close()
        print("Connection closed.")

if __name__ == '__main__':
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    deployment_name = "deployment-worker"  # Nombre de tu Deployment
    namespace = "default"  # Namespace donde está el Deployment
    intervalo_segundos = 30  # Intervalo en segundos entre cada actualización

    # Iniciar la función periódica en un hilo separado
    update_thread = threading.Thread(target=ejecutar_periodicamente, args=(deployment_name, namespace, intervalo_segundos), daemon=True)
    update_thread.start()

    run_rabbitmq()  # Mantiene el hilo principal bloqueado con RabbitMQ






