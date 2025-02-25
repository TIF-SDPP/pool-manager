import redis
import json

class RedisUtils:
    def __init__(self, host='service-redis.default.svc.cluster.local', port=6379, db=0, password='eYVX7EwVmmxKPCDmwMtyKVge8oLd2t81'):
        """Initialize Redis connection with security."""
        self.redis_client = redis.StrictRedis(host=host, port=port, db=db, password=password, decode_responses=True)

    def post_message(self, message, list_key='blockchain'):
        """Serialize and add a message to the beginning of a Redis list."""
        message_json = json.dumps(message)
        self.redis_client.lpush(list_key, message_json)

    def get_recent_messages(self, list_key='blockchain', count=10):
        """Retrieve the last 'count' messages from a Redis list."""
        messages_json = self.redis_client.lrange(list_key, 0, count - 1)
        return [json.loads(msg) for msg in messages_json]

    def get_latest_element(self, list_key='blockchain'):
        """Retrieve the latest element from a Redis list."""
        latest_element_json = self.redis_client.lindex(list_key, 0)
        if latest_element_json:
            return json.loads(latest_element_json)
        return None  # Return None if the list is empty

    def exists_id(self, id, list_key='blockchain'):
        """Check if an ID exists in the list."""
        messages_json = self.redis_client.lrange(list_key, 0, -1)  # Retrieve all messages
        for msg_json in messages_json:
            msg = json.loads(msg_json)
            if 'id' in msg and msg['id'] == id:
                return True
        return False

    def exists_worker(self, worker_id):
        """Check if a worker ID exists in Redis."""
        return self.redis_client.exists(f"workers:{worker_id}") > 0

    def setex(self, key, ttl, value):
        """Set a key with a TTL (time-to-live)."""
        return self.redis_client.setex(key, ttl, value)

    def get_active_workers(self, prefix="workers:*"):
        """Devuelve la cantidad de workers activos en Redis según el prefijo."""
        try:
            keys = list(self.redis_client.scan_iter(prefix))  # Buscamos con el prefijo correcto
            return len(keys)  # Retornamos la cantidad de workers activos
        except Exception as e:
            print(f"Error obteniendo workers activos de Redis: {e}")
            return 0  # Retornamos 0 en caso de error
        
    def get_active_workers_gpu(self):
        
        # Obtener todas las claves que representan workers
        worker_keys = self.redis_client.keys("workers:*")

        gpu_workers = []

        for key in worker_keys:
            worker_type = self.redis_client.hget(key, "worker_type")
            if worker_type and worker_type != "worker_cpu":
                gpu_workers.append(key)

        print("Workers con GPU:", gpu_workers)
        return gpu_workers
    
    def actualizar_worker(self, worker_id, worker_type):
        self.redis_client.hset(f"workers:{worker_id}", mapping={"status": "alive", "worker_type": worker_type})
        self.redis_client.expire(f"workers:{worker_id}", 30)
