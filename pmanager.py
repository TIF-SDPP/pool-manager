from flask import Flask, jsonify, request
import pika
import os
import json
from flask_cors import CORS
import sys
import threading
import time
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from dotenv import load_dotenv

load_dotenv()

rabbitmq_host = os.getenv("RABBITMQ_HOST")

rabbitmq_connection = None
rabbitmq_channel = None

# Get the current script's directory
current_dir = os.path.dirname(os.path.abspath(__file__))
# Get the parent directory
parent_dir = os.path.dirname(current_dir)

print("Parent Directory:", parent_dir)
sys.path.append(parent_dir)

from redis_utils import RedisUtils
redis_utils = RedisUtils()

# Numero minimo de workers CPU activos
MIN_WORKERS_COUNT = 1
# Numero maximo de workers CPU activos
MAX_WORKERS_COUNT = 10

# --- APP side --- 
app = Flask(__name__)
CORS(app)

# Función que actualiza las réplicas en el Deployment
def actualizar_replicas_en_kubernetes(deployment_name, namespace, replicas):
    try:
        # Después (esto funciona dentro del cluster de Kubernetes)
        config.load_incluster_config()
        
        # Crear el cliente de la API de Kubernetes
        v1_apps = client.AppsV1Api()
        
        # Intentar obtener el Deployment
        deployment = v1_apps.read_namespaced_deployment(deployment_name, namespace)
        
        current_replicas = deployment.spec.replicas
        if current_replicas == replicas:
            print(f"✅ No se necesita escalar. Réplicas actuales: {current_replicas}")
            return

        # Si se obtiene, actualizar el número de réplicas
        deployment.spec.replicas = replicas
        
        # Aplicar el cambio
        v1_apps.replace_namespaced_deployment(deployment_name, namespace, deployment)
        print(f"El número de réplicas de {deployment_name} en el namespace {namespace} ha sido actualizado a {replicas}.")
    
    except ApiException as e:
        if e.status == 404:
            # Si el Deployment no existe, puedes hacer algo aquí, por ejemplo:
            print(f"El Deployment {deployment_name} no existe en el namespace {namespace}. Intentando crear uno nuevo...")
            
            # Aquí iría la lógica para crear el Deployment si lo deseas, por ejemplo:
            # Crear un Deployment por defecto o algún comportamiento según el caso.
            # Esto lo puedes implementar dependiendo de tus necesidades.
        else:
            # Si ocurre otro tipo de error, lo reportamos.
            print(f"Ocurrió un error inesperado al intentar actualizar el Deployment: {e}")

# Función principal que se ejecuta cada X segundos
def ejecutar_periodicamente(deployment_name, namespace, intervalo_segundos):
    
    while True:
        # Obtenemos los workers GPU activos
        workers_gpu = redis_utils.get_active_workers_gpu()
        print("Workers GPU: ", workers_gpu)
        try:
            pending_tasks = get_pending_tasks()
        except Exception as e:
            print("Redis no responde, usando fallback...")
            pending_tasks = 5  # fallback temporal

        if len(workers_gpu) > 0:
                # Si hay workers GPU activos, apagamos los CPU
                actualizar_replicas_en_kubernetes(deployment_name, namespace, 0)
        else:
            if pending_tasks == 0:
                # Escalar al mínimo solo si no hay tareas
                desired_replicas = MIN_WORKERS_COUNT
            else:
                # Ajustar proporcionalmente, por ejemplo, 1 worker cada 5 tareas (ajustable)
                desired_replicas = min(max(pending_tasks // 5, MIN_WORKERS_COUNT), MAX_WORKERS_COUNT)  # Límite superior opcional

            actualizar_replicas_en_kubernetes(deployment_name, namespace, desired_replicas)

        print(f"⏳ Esperando {intervalo_segundos} segundos para la próxima verificación...")
        time.sleep(intervalo_segundos)

@app.route('/keep_alive', methods=['POST'])
def check_status():
    data = request.get_json()
    worker_id = data.get("worker_id")  
    is_user = data.get("worker_user") == "true"  # Convertir a booleano
    worker_type = data.get("worker_type")

    if not worker_id:
        return jsonify({'error': 'Missing worker_id'}), 400

    # Guardar o actualizar el worker con TTL de 30s
    redis_utils.actualizar_worker(worker_id, worker_type)

    # Guardar la categoría (nube/local)
    if not is_user:
        redis_utils.setex(f"workers_cloud:{worker_id}", 30, "alive")
    else:
        redis_utils.setex(f"workers_local:{worker_id}", 30, "alive")

    print(f"Worker {worker_id} registered. Local: {is_user}")
    return jsonify({'status': 'OK'})

def connect_rabbitmq():
    while True:
        try:
            connection = pika.BlockingConnection(pika.ConnectionParameters(
                host=rabbitmq_host,
                port=5672,
                credentials=pika.PlainCredentials('guest', 'guest')
            ))
            return connection
        except pika.exceptions.AMQPConnectionError:
            print("Fallo en la conexión, reintentando en 5 segundos...")
            time.sleep(5)

def on_message_received(ch, method, properties, body):
    global rabbitmq_channel
    
    data = json.loads(body)
    print(f"Message {data} received")

    num_workers = redis_utils.get_active_workers()
    num_workers = max(num_workers, 1)
    print(f"Active workers: {num_workers}")

    escalar = min(num_workers, 10)

    max_value = data['random_num_max']
    step = max_value // escalar

    for i in range(escalar):
        task_data = {
            "id": data['id'],
            "transactions": data['transactions'],
            "prefix": data["prefix"],
            "base_string_chain": data['base_string_chain'],
            "blockchain_content": data['blockchain_content'],
            "random_start": i * step,
            "random_end": (i + 1) * step if i < escalar - 1 else max_value
        }

        # Publicar subtarea usando el canal global
        rabbitmq_channel.basic_publish(
            exchange='workers_queue',
            routing_key='hash_task',
            body=json.dumps(task_data)
        )
        print(f"Subtask {i+1}/{escalar} sent: {task_data['random_start']} - {task_data['random_end']}")

    ch.basic_ack(delivery_tag=method.delivery_tag)

@app.route("/status")
def status():
    return jsonify({
        "pending_tasks": get_pending_tasks(),
        "workers_cpu": redis_utils.get_active_workers_cpu(),
        "workers_gpu": redis_utils.get_active_workers_gpu(),
    })

def run_flask():
    """Ejecuta Flask en un hilo separado."""
    app.run(host='0.0.0.0', port=8080, debug=True, use_reloader=False)

def run_rabbitmq():

    global rabbitmq_connection, rabbitmq_channel
    """Conecta a RabbitMQ y empieza a consumir mensajes."""
    rabbitmq_connection = connect_rabbitmq()
    rabbitmq_channel = rabbitmq_connection.channel()
    rabbitmq_channel.exchange_declare(exchange='workers_queue', exchange_type='topic', durable=True)
    rabbitmq_channel.queue_declare(queue='challenge_queue', durable=True)
    rabbitmq_channel.queue_bind(exchange='block_challenge', queue='challenge_queue', routing_key='blocks')
    
    while True:
        try:
            rabbitmq_channel.basic_consume(queue='challenge_queue', on_message_callback=on_message_received, auto_ack=False)
            print('Waiting for messages. To exit press CTRL+C')
            rabbitmq_channel.start_consuming()
        except pika.exceptions.AMQPConnectionError as e:
            print(f"Error de conexión con RabbitMQ: {e}. Intentando reconectar...")
            if rabbitmq_connection:
                rabbitmq_connection.close()
            rabbitmq_connection.close()
            time.sleep(5)  # Esperar antes de reconectar
            rabbitmq_connection = connect_rabbitmq()
            rabbitmq_channel = rabbitmq_connection.channel()
        except KeyboardInterrupt:
            print("Consumo detenido por el usuario.")
            rabbitmq_connection.close()
            print("Conexión cerrada.")
            break

def get_pending_tasks():
    queue = rabbitmq_channel.queue_declare(queue='workers_queue', passive=True)
    return queue.method.message_count

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






