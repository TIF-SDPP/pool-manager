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
    try:
        # Después (esto funciona dentro del cluster de Kubernetes)
        config.load_incluster_config()
        
        # Crear el cliente de la API de Kubernetes
        v1_apps = client.AppsV1Api()
        
        # Intentar obtener el Deployment
        deployment = v1_apps.read_namespaced_deployment(deployment_name, namespace)
        
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

        # Si hay menos del mínimo requerido, escalar
        if len(workers_gpu) == 0:
            if len(redis_utils.get_active_workers_cpu()) < 5:
                print("Workers CPU", len(redis_utils.get_active_workers_cpu()))
                actualizar_replicas_en_kubernetes(deployment_name, namespace, MIN_WORKERS_COUNT)
            else:
                print("Numero de workers CPU: OK")
        else:
            print("Workers GPU else: ", workers_gpu)
            print("Workers CPU else: ", len(redis_utils.get_active_workers_cpu()))
            actualizar_replicas_en_kubernetes(deployment_name, namespace, 0)

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
    data = json.loads(body)
    print(f"Message {data} received")

    # Obtener cantidad de workers activos
    num_workers = redis_utils.get_active_workers()
    num_workers = max(num_workers, 1)  # Evitar división por cero

    print(f"Active workers: {num_workers}")

    connection = connect_rabbitmq()
    channel = connection.channel()

    escalar = 5

    # Dividir el rango de números aleatorios según el escalar
    max_value = data['random_num_max']
    step = max_value // escalar

    # Obtenemos los workers GPU activos
    workers_gpu = redis_utils.get_active_workers_gpu()

    # Si no hay workers GPU, reducimos la complejidad del prefijo
    if len(workers_gpu) == 0:
        prefix = "000"
    else:
        prefix = data['prefix']

    for i in range(escalar):
        task_data = {
            "id": data['id'],
            "transactions": data['transactions'],
            "prefix": prefix,
            "base_string_chain": data['base_string_chain'],
            "blockchain_content": data['blockchain_content'],
            "random_start": i * step,
            "random_end": (i + 1) * step if i < escalar - 1 else max_value
        }

        # Publicar la subtarea en RabbitMQ
        channel.basic_publish(
            exchange='workers_queue',
            routing_key='hash_task',
            body=json.dumps(task_data)
        )
        print(f"Subtask {i+1}/{escalar} sent: {task_data['random_start']} - {task_data['random_end']}")

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
    
    while True:
        try:
            channel.basic_consume(queue='challenge_queue', on_message_callback=on_message_received, auto_ack=False)
            print('Waiting for messages. To exit press CTRL+C')
            channel.start_consuming()
        except pika.exceptions.AMQPConnectionError as e:
            print(f"Error de conexión con RabbitMQ: {e}. Intentando reconectar...")
            connection.close()
            time.sleep(5)  # Esperar antes de reconectar
            connection = connect_rabbitmq()
            channel = connection.channel()
        except KeyboardInterrupt:
            print("Consumo detenido por el usuario.")
            connection.close()
            print("Conexión cerrada.")
            break

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






