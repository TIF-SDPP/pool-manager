from flask import Flask, jsonify, request
import pika
import os
import json
from flask_cors import CORS
import sys
import threading

# Get the current script's directory
current_dir = os.path.dirname(os.path.abspath(__file__))
# Get the parent directory
parent_dir = os.path.dirname(current_dir)

print("Parent Directory:", parent_dir)
sys.path.append(parent_dir)

from redis_utils import RedisUtils
redis_utils = RedisUtils()

# --- APP side --- 
app = Flask(__name__)
CORS(app)

@app.route('/keep_alive', methods=['POST'])
def check_status():
    data = request.get_json()
    worker_id = data.get("worker_id")  # Obtener el ID del worker desde el body

    if not worker_id:
        return jsonify({'error': 'Missing worker_id'}), 400

    # Verificar si el worker ya existe en Redis
    if redis_utils.exists_worker(worker_id):
        print(f"Worker {worker_id} refreshed")
    else:
        print(f"New worker {worker_id} registered")

    # Guardar o actualizar el worker con TTL de 30s
    redis_utils.setex(f"workers:{worker_id}", 15, "alive")

    return jsonify({'status': 'OK'})

def on_message_received(ch, method, properties, body):
    data = json.loads(body)
    print(f"Message {data} received")

    # Obtener cantidad de workers activos
    num_workers = redis_utils.get_active_workers()
    num_workers = max(num_workers, 1)  # Evitar división por cero

    print(f"Active workers: {num_workers}")

    connection = pika.BlockingConnection(
        pika.ConnectionParameters(host='rabbit1', port=5672, credentials=pika.PlainCredentials('rabbitmq', 'rabbitmq'))
    )
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
    connection = pika.BlockingConnection(
        pika.ConnectionParameters(host='rabbit1', port=5672, credentials=pika.PlainCredentials('rabbitmq', 'rabbitmq'))
    )
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
    flask_thread = threading.Thread(target=run_flask, daemon=True)  # Hilo daemon para Flask
    flask_thread.start()

    run_rabbitmq()  # Ejecuta RabbitMQ en el hilo principal




