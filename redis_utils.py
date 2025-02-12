import redis
import json

class RedisUtils:
    def __init__(self, host='cache', port=6379, db=0, password='eYVX7EwVmmxKPCDmwMtyKVge8oLd2t81'):
        """Initialize Redis connection with security."""
        self.redis_client = redis.StrictRedis(host=host, port=port, db=db, password=password)

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
    
    def get_active_workers(self):
        """Devuelve una lista de workers activos en Redis."""
        try:
            keys = list(self.redis_client.scan_iter("workers:*"))  # Usamos scan_iter() para mejor rendimiento
            return len(keys)  # Devolvemos la cantidad de workers activos
        except Exception as e:
            print(f"Error obteniendo workers activos de Redis: {e}")
            return 0  # Retornamos 0 en caso de error

# The module can be used after import by creating an instance of RedisUtils