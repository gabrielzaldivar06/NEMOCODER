/**
 * ObjectiveDefinition Modal Component
 * Allows users to create and define a new objective
 */

import React, { useState } from "react";
import { Target, X, Plus, Trash2 } from "lucide-react";
import { ObjectiveState } from "../services/planNemoClient";

interface ObjectiveDefinitionProps {
  isOpen: boolean;
  onClose: () => void;
  onCreate: (title: string, description: string, criteria: string[]) => void;
}

export function ObjectiveDefinition({
  isOpen,
  onClose,
  onCreate,
}: ObjectiveDefinitionProps) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [criteria, setCriteria] = useState<string[]>([""]);

  const handleAddCriteria = () => {
    setCriteria([...criteria, ""]);
  };

  const handleRemoveCriteria = (index: number) => {
    setCriteria(criteria.filter((_, i) => i !== index));
  };

  const handleCriteriaChange = (index: number, value: string) => {
    const updated = [...criteria];
    updated[index] = value;
    setCriteria(updated);
  };

  const handleCreate = () => {
    if (!title.trim()) {
      alert("Por favor, ingresa un título para el objetivo");
      return;
    }

    const validCriteria = criteria.filter((c) => c.trim());
    if (validCriteria.length === 0) {
      alert("Por favor, define al menos un criterio de aceptación");
      return;
    }

    onCreate(title, description, validCriteria);

    // Reset form
    setTitle("");
    setDescription("");
    setCriteria([""]);
    onClose();
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-1000">
      <div className="bg-gray-900 border border-gray-700 rounded-lg shadow-2xl max-w-2xl w-full mx-4 max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="sticky top-0 bg-gradient-to-r from-gray-900 to-gray-800 border-b border-gray-700 p-6 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Target size={24} className="text-blue-400" />
            <h2 className="text-xl font-bold text-white">Definir Objetivo</h2>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white transition-colors"
          >
            <X size={24} />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-6">
          {/* Title */}
          <div>
            <label className="block text-sm font-semibold text-gray-300 mb-2">
              Título del Objetivo
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="ej: Construir CLI tool para gestionar tareas"
              className="w-full px-4 py-2 bg-gray-800 border border-gray-600 rounded text-white placeholder-gray-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
            />
          </div>

          {/* Description */}
          <div>
            <label className="block text-sm font-semibold text-gray-300 mb-2">
              Descripción (Opcional)
            </label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Proporciona contexto y detalles del objetivo..."
              rows={3}
              className="w-full px-4 py-2 bg-gray-800 border border-gray-600 rounded text-white placeholder-gray-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
            />
          </div>

          {/* Acceptance Criteria */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="block text-sm font-semibold text-gray-300">
                Criterios de Aceptación
              </label>
              <button
                onClick={handleAddCriteria}
                className="flex items-center gap-1 px-2 py-1 text-xs text-blue-400 hover:text-blue-300 transition-colors"
              >
                <Plus size={16} />
                Agregar
              </button>
            </div>

            <div className="space-y-2">
              {criteria.map((criterion, index) => (
                <div key={index} className="flex gap-2">
                  <input
                    type="text"
                    value={criterion}
                    onChange={(e) => handleCriteriaChange(index, e.target.value)}
                    placeholder={`Criterio ${index + 1}: ej: "API responde en <100ms"`}
                    className="flex-1 px-4 py-2 bg-gray-800 border border-gray-600 rounded text-white placeholder-gray-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 text-sm"
                  />
                  {criteria.length > 1 && (
                    <button
                      onClick={() => handleRemoveCriteria(index)}
                      className="text-red-400 hover:text-red-300 transition-colors p-2"
                    >
                      <Trash2 size={16} />
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="bg-gradient-to-r from-gray-900 to-gray-800 border-t border-gray-700 p-6 flex items-center justify-end gap-3">
          <button
            onClick={onClose}
            className="px-6 py-2 text-gray-300 hover:text-white transition-colors"
          >
            Cancelar
          </button>
          <button
            onClick={handleCreate}
            className="px-6 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded font-semibold transition-colors"
          >
            Crear Objetivo
          </button>
        </div>
      </div>
    </div>
  );
}
