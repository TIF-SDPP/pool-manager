import redis
import time
import json

class RedisUtils:
    def __init__(self, host='service-redis.default.svc.cluster.local', port=6379, db=0, password='eYVX7EwVmmxKPCDmwMtyKVge8oLd2t81'):
        """Initialize Redis connection with security and retry mechanism."""
        self.host = host
        self.port = port
        self.db = db
        self.password = password
        self.redis_client = None
        self.retry_interval = 10
        self._connect()

    def _connect(self):
        """Try to connect to Redis with retries."""
        while True:
            try:
                # Intentar conectarse a Redis
                self.redis_client = redis.StrictRedis(host=self.host, port=self.port, db=self.db, password=self.password)
                self.redis_client.ping()  # Verificar que Redis esté respondiendo
                print("Conexión a Redis establecida exitosamente.")
                break
            except redis.ConnectionError:
                print("Error de conexión a Redis. Reintentando en {} segundos...".format(self.retry_interval))
                time.sleep(self.retry_interval)

    def post_message(self, message, list_key='blockchain'):
        """Serialize and add a message to the beginning of a Redis list."""
        try:
            message_json = json.dumps(message)
            self.redis_client.lpush(list_key, message_json)
        except redis.RedisError as e:
            print(f"Error al publicar mensaje en Redis: {e}")
            self.connect_to_redis()  # Try reconnecting and retry the operation
            self.post_message(message, list_key)

    def get_recent_messages(self, list_key='blockchain', count=10):
        """Retrieve the last 'count' messages from a Redis list."""
        try:
            messages_json = self.redis_client.lrange(list_key, 0, count - 1)
            return [json.loads(msg) for msg in messages_json]
        except redis.RedisError as e:
            print(f"Error al obtener mensajes de Redis: {e}")
            self.connect_to_redis()  # Try reconnecting and retry the operation
            return self.get_recent_messages(list_key, count)

    def get_active_workers(self, prefix="workers:*"):
        """Devuelve la cantidad de workers activos en Redis según el prefijo."""
        try:
            keys = list(self.redis_client.scan_iter(prefix))  # Buscamos con el prefijo correcto
            return len(keys)  # Retornamos la cantidad de workers activos
        except redis.RedisError as e:
            print(f"Error obteniendo workers activos de Redis: {e}")
            self.connect_to_redis()  # Try reconnecting and retry the operation
            return self.get_active_workers(prefix)

    def get_active_workers_gpu(self):
        """Obtener los workers GPU activos desde Redis con su información."""
        try:
            worker_keys = self.redis_client.keys("workers:*")
            gpu_workers = {
                key: self.redis_client.hgetall(key)  # Obtiene el mapeo completo
                for key in worker_keys
                if self.redis_client.hget(key, "worker_type") != b"worker_cpu"  # Filtra por tipo GPU
            }
            return gpu_workers
        except redis.RedisError as e:
            print(f"Error obteniendo workers GPU de Redis: {e}")
            self.connect_to_redis()  # Reconectar e intentar nuevamente
            return self.get_active_workers_gpu()

    def get_active_workers_cpu(self):
        """Obtener los workers CPU activos desde Redis con su información."""
        try:
            worker_keys = self.redis_client.keys("workers:*")
            cpu_workers = {
                key: self.redis_client.hgetall(key)  # Obtiene el mapeo completo
                for key in worker_keys
                if self.redis_client.hget(key, "worker_type") != b"worker_gpu"  # Filtra por tipo CPU
            }
            return cpu_workers
        except redis.RedisError as e:
            print(f"Error obteniendo workers CPU de Redis: {e}")
            self.connect_to_redis()  # Reconectar e intentar nuevamente
            return self.get_active_workers_cpu()


    def setex(self, key, ttl, value):
        """Set a key with a TTL (time-to-live)."""
        try:
            return self.redis_client.setex(key, ttl, value)
        except redis.RedisError as e:
            print(f"Error al establecer clave en Redis: {e}")
            self.connect_to_redis()  # Try reconnecting and retry the operation
            return self.setex(key, ttl, value)

    def actualizar_worker(self, worker_id, worker_type):
        """Actualizar el estado de un worker en Redis."""
        try:
            self.redis_client.hset(f"workers:{worker_id}", mapping={"status": "alive", "worker_type": worker_type})
            self.redis_client.expire(f"workers:{worker_id}", 30)
        except redis.RedisError as e:
            print(f"Error al actualizar worker en Redis: {e}")
            self.connect_to_redis()  # Try reconnecting and retry the operation
            self.actualizar_worker(worker_id, worker_type)
