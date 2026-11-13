-- Esquema de MySQL (AWS RDS): solo se usa la tabla usuarios para el login.
-- La telemetria vive en DynamoDB (tabla parqueo_mensajes). Las tablas plazas/lecturas son de la Unidad 2.
CREATE DATABASE IF NOT EXISTS estacionamiento;
USE estacionamiento;
CREATE USER IF NOT EXISTS 'apiuser'@'%' IDENTIFIED BY 'CAMBIAR_CLAVE' REQUIRE SSL;
GRANT SELECT, INSERT, UPDATE ON estacionamiento.* TO 'apiuser'@'%';
CREATE TABLE IF NOT EXISTS usuarios (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(50) UNIQUE NOT NULL,
  password_hash VARCHAR(100) NOT NULL,
  creado TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
