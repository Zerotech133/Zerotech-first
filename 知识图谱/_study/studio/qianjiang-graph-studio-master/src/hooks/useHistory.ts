import { IAbstractGraph as IGraph } from '@antv/g6-core'
import { clone } from '@antv/util'
import {useState, useEffect} from 'react'

export default function useHistory (graph: IGraph) {
  const [historyInfo, setHistoryInfo] = useState({
    currentStep: 0,
    allStep: 0
  })

  useEffect(() => {
    graph.on('stackchange', (evt) => {
      const { undoStack, redoStack } = evt
      const undoStackLen = (undoStack as any).length
      const redoStackLen = (redoStack as any).length
      setHistoryInfo({
        allStep: undoStackLen + redoStackLen,
        currentStep: undoStackLen
      })
    })
  }, [graph])

  return {
    historyInfo,
    redo: () => redo(graph),
    undo: () => undo(graph)
  }
}

function redo(graph: IGraph) {
  const redoStack = graph.getRedoStack()

  if (!redoStack || redoStack.length === 0) {
    return
  }

  const currentData = redoStack.pop()
  if (currentData) {
    const { action } = currentData
    let data = currentData.data.after
    graph.pushStack(action, clone(currentData.data))
    if (action === 'delete') {
      data = currentData.data.before
    }

    if (!data) return

    // const autoPaint = graph.get('autoPaint');
    // graph.setAutoPaint(false);
    // graph.destroy()

    switch (action) {
      case 'visible': {
        Object.keys(data).forEach((key) => {
          const array = data[key]
          if (!array) return
          array.forEach((model: any) => {
            const item = graph.findById(model.id)
            if (model.visible) {
              graph.showItem(item, false)
            } else {
              graph.hideItem(item, false)
            }
          })
        })
        break
      }
      case 'render':
      case 'update':
        Object.keys(data).forEach((key) => {
          const array = data[key]
          if (!array) return
          array.forEach((model: any) => {
            graph.updateItem(model.id, model, false)
          })
        })
        break
      case 'changedata':
        graph.changeData(data, false)
        // graph.read(data)
        break
      case 'delete':
        if (data.edges) {
          data.edges.forEach((model: any) => {
            graph.removeItem(model.id, false)
          })
        }
        if (data.nodes) {
          data.nodes.forEach((model: any) => {
            graph.removeItem(model.id, false)
          })
        }
        if (data.combos) {
          data.combos.forEach((model: any) => {
            graph.removeItem(model.id, false)
          })
        }
        break
      case 'add': {
        Object.keys(data).forEach((key) => {
          const array = data[key]
          if (!array) return
          array.forEach((model: any) => {
            const itemType = model.itemType
            delete model.itemType
            graph.addItem(itemType, model, false)
          })
        })
        break
      }
      case 'updateComboTree':
        Object.keys(data).forEach((key) => {
          const array = data[key]
          if (!array) return
          array.forEach((model: any) => {
            graph.updateComboTree(model.id, model.parentId, false)
          })
        })
        break
      default:
    }
    setTimeout(() => {
      // graph.fitCenter()
      // graph.paint();
      // graph.setAutoPaint(autoPaint);
      console.log('refresh')
      // graph.refresh();
      // graph.render()
      // graph.read(data)
    }, 3000)
    return data
  }
}

function undo(graph: IGraph) {
  const undoStack = graph.getUndoStack()
  if (!undoStack || undoStack.length === 1) {
    return
  }

  const currentData = undoStack.pop()
  if (currentData) {
    const { action } = currentData
    graph.pushStack(action, clone(currentData.data), 'redo')
    let data = currentData.data.before

    if (action === 'add') {
      data = currentData.data.after
    }

    if (!data) return
    // const autoPaint = graph.get('autoPaint');
    // graph.setAutoPaint(false);

    switch (action) {
      case 'visible': {
        Object.keys(data).forEach((key) => {
          const array = data[key]
          if (!array) return
          array.forEach((model: any) => {
            const item = graph.findById(model.id)
            if (model.visible) {
              graph.showItem(item, false)
            } else {
              graph.hideItem(item, false)
            }
          })
        })
        break
      }
      case 'render':
      case 'update':
        Object.keys(data).forEach((key) => {
          const array = data[key]
          if (!array) return
          array.forEach((model: any) => {
            graph.updateItem(model.id, model, false)
          })
        })
        break
      case 'changedata':
        graph.changeData(data, false)
        // graph.read(data)
        break
      case 'delete': {
        Object.keys(data).forEach((key) => {
          const array = data[key]
          if (!array) return
          array.forEach((model: any) => {
            const itemType = model.itemType
            delete model.itemType
            graph.addItem(itemType, model, false)
          })
        })
        break
      }
      case 'add':
        Object.keys(data).forEach((key) => {
          const array = data[key]
          if (!array) return
          array.forEach((model: any) => {
            graph.removeItem(model.id, false)
          })
        })
        break
      case 'updateComboTree':
        Object.keys(data).forEach((key) => {
          const array = data[key]
          if (!array) return
          array.forEach((model: any) => {
            graph.updateComboTree(model.id, model.parentId, false)
          })
        })
        break
      default:
    }
    setTimeout(() => {
      console.log('refresh')
      // graph.fitCenter()
      // graph.paint();
      // graph.setAutoPaint(autoPaint);
      // graph.refresh();
      // graph.render()
      // graph.read(data)
      // graph.updateLayout('force'); // 参数为 String 代表布局名称
    }, 3000)
    return data
  }
}

