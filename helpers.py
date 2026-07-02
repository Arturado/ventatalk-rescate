import re


def es_menor_de_edad(edad_aproximada: str = None, edad_texto: str = None) -> bool:
    """
    Detecta si una persona es menor de edad.
    - edad_aproximada: valor del selector ("nino", "joven", "adulto", etc.)
    - edad_texto: texto libre de edad ("8 años", "niño", "nino de 5", "15", etc.)
    """
    if edad_aproximada and edad_aproximada.lower() in ("nino", "niño"):
        return True

    if edad_texto:
        edad_lower = edad_texto.lower().strip()
        # Detectar palabras clave
        if any(p in edad_lower for p in ["niño", "nino", "nina", "niña", "bebe", "bebé", "infante"]):
            return True
        # Detectar números menores a 18
        numeros = re.findall(r'\b(\d{1,2})\b', edad_texto)
        for n in numeros:
            if int(n) < 18:
                return True
    return False


def enmascarar_cedula(cedula: str) -> str:
    """Muestra solo los últimos 4 dígitos."""
    if not cedula:
        return None
    cedula = str(cedula).strip()
    if len(cedula) <= 4:
        return cedula
    return "*" * (len(cedula) - 4) + cedula[-4:]


def enmascarar_email(email: str) -> str:
    """Enmascara email: u***@gmail.com"""
    if not email:
        return None
    try:
        user, domain = email.split("@", 1)
        if len(user) <= 1:
            return f"{user}***@{domain}"
        return f"{user[0]}***@{domain}"
    except Exception:
        return "***"
