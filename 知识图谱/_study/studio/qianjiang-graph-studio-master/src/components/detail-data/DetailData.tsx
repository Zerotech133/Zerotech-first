import React, {useEffect, useState, useContext} from "react"
import {Drawer} from "antd"
import {GraphinContext} from "@antv/graphin"
import {IG6GraphEvent} from "@antv/g6"

export const DetailData: React.FC = () => {

  const [drawerVisible, setDrawerVisible] = useState(false)
  const [drawerData, setDrawerData] = useState<{ [key: string]: string }>({})
  const {graph} = useContext(GraphinContext)

  useEffect(() => {
    const handleClick = (evt: IG6GraphEvent) => {
      if (evt.item === null) {
        return
      }
      if ((evt.originalEvent as MouseEvent).ctrlKey) {
        return
      }
      const model = evt.item.getModel()
      if (!model.attrs) return
      setDrawerData(model.attrs as { [key: string]: string })
      setDrawerVisible(true)
    }
    graph.on('node:click', handleClick)
    graph.on('edge:click', handleClick)
    return () => {
      graph.off('node:click', handleClick)
      graph.off('edge:click', handleClick)
    }
  }, [graph])

  const onDrawerClose = () => {
    setDrawerVisible(false)
  }

  return (
    <Drawer title="数据详情" placement="right" onClose={onDrawerClose} visible={drawerVisible}>
      {
        Object.keys(drawerData).map(it => (
          <p key={it}>{it}:{drawerData[it]}</p>
        ))
      }
    </Drawer>
  )
}
