from dataclasses import dataclass


@dataclass
class PoolClosedError(Exception):
    message: str = "The connection pool has been closed unexpectedly. This typically happens when either the pool.close() was called elsewhere in the program or an unrecoverable error occurred. Please check your application logs for errors that may have triggered this closure."


@dataclass
class PoolConnectionAcquireTimeoutError(Exception):
    message: str = "Failed to acquire connection from pool within timeout period. This may indicate that all connections are in use or the pool is under heavy load."
