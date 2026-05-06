# Reflexion Critica Del PRD

## Juicio General

La decision principal es correcta: usar Aider como base activa y extenderlo con capacidades de OpenCode, OpenHands y NEMO es mejor que crear un agente desde cero. Aider ya resuelve el problema mas dificil del coding agentico fiable: editar repos reales con contexto, diffs, git y validacion. NEMO ya resuelve memoria persistente y contexto operativo. Por eso el producto debe enfocarse en orquestacion, runtime, permisos, UX y memoria integrada, no en rehacer motores ya existentes.

Pero el PRD tiene algunos sesgos sanos para una vision ambiciosa y peligrosos para ejecucion rapida. La correccion no es abandonar la vision, sino reducir el camino critico.

## Sesgo 1: "Aider Y NEMO Ya Estan Terminados, Por Lo Tanto La Integracion Es Simple"

Esto es parcialmente falso. Que dos productos esten maduros no vuelve trivial su integracion. Lo dificil no es llamar funciones; lo dificil es convertirlos en un sistema coherente con estado, permisos, auditoria, lifecycle, UX y recuperacion ante fallos.

Correccion:

- Tratar Aider y NEMO como motores confiables, no como modulos plug-and-play.
- Diseñar adapters finos con contratos claros.
- Evitar tocar internals de Aider hasta tener una frontera estable.
- Medir integracion con una vertical slice real: tarea -> contexto NEMO -> plan -> patch Aider -> tests -> review -> memoria final.

## Sesgo 2: "Desktop App Es El Producto, Entonces Debemos Construir Desktop Pronto"

Desktop es la superficie final, pero no debe ser el primer riesgo a resolver. El riesgo principal no es pintar una UI; es que el agente pueda ejecutar cambios reales con autonomia, memoria, permisos y validacion sin perder control.

Correccion:

- Primero construir un backend/run loop headless reproducible.
- La UI desktop debe consumir eventos y contratos ya probados.
- Evitar una app visual bonita que todavia no pueda completar tareas de punta a punta.

## Sesgo 3: "Copiar Lo Mejor De OpenHands/OpenCode"

Copiar demasiado puede generar un producto pesado. OpenHands es potente, pero tambien trae complejidad de runtime, containers, browser/computer tools y multi-step autonomy. OpenCode aporta ergonomia y permisos, pero no todo su modelo debe entrar de golpe.

Correccion:

- Importar principios, no sistemas completos.
- De OpenCode: fases, permisos, configuracion de agentes y comandos.
- De OpenHands: runtime state, sandbox lifecycle, event stream y long-running tasks.
- Posponer browser/computer tools y contenedores hasta que worktree runtime sea solido.

## Sesgo 4: "Alta Autonomia Es El Objetivo Principal"

La alta autonomia si es una caracteristica principal, especialmente Full Handoff desde PRD/specs durante horas sin interaccion humana. El sesgo a corregir no es querer autonomia alta; el sesgo es creer que se obtiene simplemente dando mas permisos. La autonomia confiable no nace de permitir mas acciones; nace de cerrar bien ciclos pequeños, medirlos y luego permitir continuidad prolongada dentro de limites preaprobados.

Correccion:

- Definir autonomia como una propiedad ganada por evidencias.
- Subir niveles solo cuando las metricas sean buenas: validation pass rate, rollback rate, permission incidents, memory usefulness.
- Mantener el primer prototipo en Autonomous Sandbox con merge gate humano, pero diseñar desde ya el contrato `full_handoff` para ejecucion prolongada sin prompts dentro del sandbox.
- Separar "sin interaccion humana durante build" de "merge sin review". El primero es requisito principal; el segundo debe seguir bloqueado.

## Sesgo 5: "NEMO Debe Usarse Mucho"

NEMO debe usarse siempre, pero no ruidosamente. Si cada accion escribe memoria, la memoria se degrada. Si cada fase recupera demasiado contexto, el agente pierde foco.

Correccion:

- Usar NEMO de forma ritual pero economica.
- Inicio: contexto compacto.
- Durante build: expandir solo evidencia necesaria.
- Review/close: guardar solo decisiones, correcciones, resultados y lecciones reutilizables.
- Convertir memory writeback en artefacto revisable.

## Mejores Opciones Consideradas

### Opcion A: Fork Profundo De Aider

Ventaja: control total.

Problema: deuda de mantenimiento alta y riesgo de romper upstream.

Veredicto: evitar al inicio. Mejor extension por capas bajo `aider.nemo_platform` y adapters.

### Opcion B: Aider Como Subproceso Externo

Ventaja: menos fork.

Problema: peor control de estado, permisos, memoria y UX integrada.

Veredicto: util como fallback, no como arquitectura principal.

### Opcion C: Construir Backend Nuevo Y Usar Aider Solo Como Referencia

Ventaja: arquitectura limpia.

Problema: perderiamos la ventaja principal: Aider ya edita bien.

Veredicto: mala opcion para llegar rapido.

### Opcion D: OpenHands Como Base Principal

Ventaja: autonomia fuerte desde el inicio.

Problema: menos enfocado a edicion quirurgica fiable estilo Aider, mas superficie de complejidad.

Veredicto: no como base. Tomar runtime concepts.

### Opcion E: OpenCode Como Base Principal

Ventaja: workflow moderno y ergonomico.

Problema: para este objetivo, la calidad de edicion de Aider y la memoria NEMO pesan mas.

Veredicto: tomar permisos/workflow, no base.

## Recomendacion Corregida

La decision base se mantiene, pero el orden debe endurecerse:

1. Aider fork extension layer.
2. Aider CLI platform introspection.
3. Task/Run event model.
4. NEMO adapter real con lifecycle minimo.
5. Worktree runtime.
6. End-to-end headless task run.
7. Desktop UI sobre eventos reales.
8. Background agents y subagentes.
9. Contenedores/browser tools.

La primera meta no debe ser "tener una app desktop". Debe ser "un run headless reproducible que haga una tarea real de codigo con memoria, permisos, sandbox, patch, tests y review".

## Cambios Que Haria Al PRD

1. Cambiar "Desktop MVP" por "Headless Run MVP primero, Desktop MVP despues".
2. Definir Aider integration boundary antes de tocar flujos profundos.
3. Agregar metricas de autonomia desde el principio.
4. Tratar memory writeback como reviewable artifact.
5. Posponer contenedores, browser tools y multi-agent complejo.
6. Hacer que la primera demo real sea una tarea pequeña en un repo real, no una pantalla.
7. Agregar Full Handoff como spec principal: PRD -> specs -> tests -> implementacion -> validacion -> repair -> review package -> NEMO writeback.

## Conclusion

Las decisiones son buenas para el objetivo, pero necesitan una disciplina mas dura contra el exceso de ambicion temprana. El mejor producto no sale de sumar Aider + OpenCode + OpenHands + NEMO completos de golpe; sale de construir una columna vertebral pequeña donde cada uno aporta exactamente su fortaleza.

La formula corregida es:

> Aider escribe, NEMO recuerda, OpenCode gobierna el workflow, OpenHands inspira el runtime, y la desktop UI observa y controla un sistema que ya funciona.
