#!/bin/bash

# Script para aplicar el deployment de Poolmanager en Kubernetes

# Aplicar el archivo de configuración de Kubernetes
kubectl apply -f ./deploy-poolmanager.yaml

# Aplicar el archivo de configuración de Kubernetes
kubectl apply -f ./service-poolmanager.yaml