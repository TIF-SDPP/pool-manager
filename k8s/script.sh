#!/bin/bash

# Script para aplicar el deployment de Pool Manager en Kubernetes

# Aplicar el archivo de configuración de Kubernetes
kubectl apply -f ./headless-poolmanager.yaml

# Aplicar el archivo de configuración de Kubernetes
kubectl apply -f ./statefulset-poolmanager.yaml

# Aplicar el archivo de configuración de Kubernetes
kubectl apply -f ./service-poolmanager.yaml