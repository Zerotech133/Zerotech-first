import React, {useEffect, useState} from "react"
import {Button, Card, Col, Row, Slider} from "antd"
import {SketchPicker} from 'react-color'
import styles from './CustomStyle.module.css'
import {GraphinContext} from "@antv/graphin"
import {defaultNodeStyle, defaultEdgeStyle} from '../../views/chart-explore/ChartExplore'
// import axios from 'axios'

type props = {
  type: 'node' | 'edge', // 右键打开更新面板的触发节点类型
  element: any, // 右键打开更新面板的触发节点
  customNodeElement: any, // 打开面板时，Node样式的参照节点
  customEdgeElement: any, // 打开面板时，Edge样式的参照节点
  activeNodes: any, // 选中的node
  activeEdges: any,  // 选中的edge
  onUpdateFinish: () => void // 更新样式面板关闭触发
  onUpdate: (style:any, activeTabKey: string) => void // 操作更新样式面板触发
}
// 更新面板全部属性名
const styleNames = ['size', 'fill', 'stroke', 'lineWidth', 'opacity', 'fillOpacity']

export const CustomStyle: React.FC<props> = ({type, element, customNodeElement, customEdgeElement,activeNodes, activeEdges, onUpdateFinish, onUpdate}) => {
  const [prevElement, setPrevElement] = useState<any>(null)
  const [showFill, setShowFill] = useState(false)
  const [showStroke, setShowStroke] = useState(false)
  // 更新样式面板切换的key
  const [activeTabKey, setActiveTabKey] = useState<string>('');


  const graphin = React.useContext(GraphinContext)
  // node或edge的默认节点样式
  const getDefaultStyle = () => type === 'node' ? {...defaultNodeStyle} : {...defaultEdgeStyle}

  const getElementStyle = (element: any) => {
    if (!element || !element._cfg) {
      return getDefaultStyle()
    }
    const style: { [key: string]: any } = {}
    const attrs = element?.getModel()?.style?.keyshape || {}

    styleNames.forEach(name => {
      style[name] = attrs[name] ? attrs[name] : getDefaultStyle()[name]
      if (style[name] === null || style[name] === undefined) {
        delete style[name]
      }
    })
    return style
  }
  // 更新样式面板node的绑定值初始化
  const customNodeElementStyle = customNodeElement ? getElementStyle(customNodeElement) : {...defaultNodeStyle}
   // 更新样式面板edge的绑定值初始化
  const customEdgeElementStyle = customEdgeElement ? getElementStyle(customEdgeElement) : {...defaultEdgeStyle}

  // 更新样式面板node的绑定值
  const [nodeStyle, setNodeStyle] = useState<any>(customNodeElementStyle)
   // 更新样式面板edge的绑定值
  const [edgeStyle, setEdgeStyle] = useState<any>(customEdgeElementStyle)

  // 更新样式面板tab值
  const [tabList] = useState([
    {
      key: 'node',
      tab: 'Node'
    },
    {
      key: 'edge',
      tab: 'Edge'
    },
  ])
  //  更新样式面板打开与关闭时触发
  if (prevElement !== element) {
    setPrevElement(element)

    setNodeStyle(customNodeElementStyle)
    setEdgeStyle(customEdgeElementStyle)
    setActiveTabKey(type)
  }

  const handleStyleChange = (key: string, value: any) => {
    const graph = graphin.graph
    if (activeTabKey === 'node') {
      let  allNodesId=graph.getNodes().map(item=>item.getModel().id) 
      // 更新面板操作时，取消选中node的selected状态
      activeNodes.forEach((node:any) => {
        if (allNodesId.includes(node.id)) {
          graph.setItemState(node.id, 'selected', false);
        }
      })
      nodeStyle[key] = value
      // 操作更新面板，选中的node样式同步更新
      onUpdate(nodeStyle, activeTabKey)
      setNodeStyle({
        ...nodeStyle,
        [key]: value
      })
    } else {
      let  allEdgesId=graph.getEdges().map(item=>item.getModel().id) 
      // 更新面板操作时，取消选中node的selected状态
      activeEdges.forEach((node:any) => {
        if (allEdgesId.includes(node.id)) {
          graph.setItemState(node.id, 'selected', false);
        }
      })
      edgeStyle[key] = value
      // 操作更新面板，选中的node样式同步更新
      onUpdate(edgeStyle, activeTabKey)

      setEdgeStyle({
        ...edgeStyle,
        [key]: value
      })
    }
  }

  useEffect(() => {
    if (!element) {
      return
    }
  }, [element])
  const cancel = () => {
    const graph = graphin.graph
    let  allNodesId=graph.getNodes().map(item=>item.getModel().id)
    // 选中的所有node节点恢复原样式
    activeNodes.forEach((node:any) => {
      let id = node.id
      // 更新面板操作前node的样式
      let originKeyshape = node.originKeyshape
      if (allNodesId.includes(node.id)) {
        graph.updateItem(id, {
          style: {
            // @ts-ignore
            keyshape: {...originKeyshape}
          }
        })
      }
    })
    let  allEdgesId=graph.getEdges().map(item=>item.getModel().id) 
    // 选中的所有edge节点恢复原样式
    activeEdges.forEach((edge:any) => {
      let id = edge.id
      // 更新面板操作前node的样式
      let originKeyshape = edge.originKeyshape
      if (allEdgesId.includes(edge.id)) {
        graph.updateItem(id, {
          style: {
            // @ts-ignore
            keyshape: {...originKeyshape}
          }
        })
      }
    })
    onUpdateFinish()
  }

  const submit = () => {
    onUpdateFinish()
  }

  if (!element) {
    return null
  }

  const nodeDom = (<>
    <Row className={styles['ant-row-margin']}>
        <Col span={10}>节点大小</Col>
        <Col span={14}>
        <div style={{display:'none'}}>{nodeStyle.size}</div>
            <Slider
                defaultValue={nodeStyle.size}
                min={10}
                max={100}
                onChange={(value: number) => handleStyleChange('size', value)}/>
        </Col>
    </Row>
    <Row className={styles['ant-row-margin']}>
      <Col span={10}>边框宽度</Col>
      <Col span={14}>
        <Slider
          defaultValue={nodeStyle.lineWidth}
          min={1}
          max={5}
          onChange={(value: number) => handleStyleChange('lineWidth', value)}/>
      </Col>
    </Row>
    <Row className={styles['ant-row-margin']}>
          <Col span={10}>填充色</Col>
          <Col span={14}>
              <div>
                  <div className={styles.swatch} onClick={() => {
                    setShowFill(true)
                  }}>
                      <div className={styles.color} style={{backgroundColor: nodeStyle.fill}}/>
                  </div>
                {showFill ? <div className={styles.popover}>
                  <div className={styles.cover} onClick={() => {
                    setShowFill(false)
                  }}/>
                  <SketchPicker color={nodeStyle.fill} onChange={value => handleStyleChange('fill', value.hex)}/>
                </div> : null}
              </div>
          </Col>
      </Row>
      <Row className={styles['ant-row-margin']}>
        <Col span={10}>描边色</Col>
        <Col span={14}>
          <div>
            <div className={styles.swatch} onClick={() => {
              setShowStroke(true)
            }}>
              <div className={styles.color} style={{backgroundColor: nodeStyle.stroke}}/>
            </div>
            {showStroke ? <div className={styles.popover}>
              <div className={styles.cover} onClick={() => {
                setShowStroke(false)
              }}/>
              <SketchPicker color={nodeStyle.stroke} onChange={value => handleStyleChange('stroke', value.hex)}/>
            </div> : null}
          </div>
        </Col>
      </Row>
      <Row className={styles['ant-row-margin']}>
        <Col span={10}>不透明度</Col>
        <Col span={14}>
          <Slider
            defaultValue={nodeStyle.opacity}
            min={0.1}
            max={1}
            step={0.1}
            onChange={(value: number) => handleStyleChange('opacity', value)}/>
        </Col>
      </Row>
      <Row className={styles['ant-row-margin']}>
            <Col span={10}>填充色不透明度</Col>
            <Col span={14}>
                <Slider
                    defaultValue={nodeStyle.fillOpacity}
                    min={0.1}
                    max={1}
                    step={0.1}
                    onChange={(value: number) => handleStyleChange('fillOpacity', value)}/>
            </Col>
      </Row>
   </>)
   const edgeDom = (<>
    <Row className={styles['ant-row-margin']}>
      <Col span={10}>边宽</Col>
      <Col span={14}>
        <Slider
          defaultValue={edgeStyle.lineWidth}
          min={1}
          max={5}
          onChange={(value: number) => handleStyleChange('lineWidth', value)}/>
      </Col>
    </Row>
    <Row className={styles['ant-row-margin']}>
      <Col span={10}>描边色</Col>
      <Col span={14}>
        <div>
          <div className={styles.swatch} onClick={() => {
            setShowStroke(true)
          }}>
            <div className={styles.color} style={{backgroundColor: edgeStyle.stroke}}/>
          </div>
          {showStroke ? <div className={styles.popover}>
            <div className={styles.cover} onClick={() => {
              setShowStroke(false)
            }}/>
            <SketchPicker color={edgeStyle.stroke} onChange={value => handleStyleChange('stroke', value.hex)}/>
          </div> : null}
        </div>
      </Col>
    </Row>
    <Row className={styles['ant-row-margin']}>
      <Col span={10}>不透明度</Col>
      <Col span={14}>
        <Slider
          defaultValue={edgeStyle.opacity}
          min={0.1}
          max={1}
          step={0.1}
          onChange={(value: number) => handleStyleChange('opacity', value)}/>
      </Col>
    </Row>
   </>)
  const contentList:any = {
    node: nodeDom,
    edge: edgeDom,
  };



  const onTabChange = (key:any) => {
    setActiveTabKey(key);
  };


  return (

    <Card
    tabIndex={-1}
    title="更新样式"
    className={styles.container}
    tabList={tabList}
    activeTabKey={activeTabKey}
    onTabChange={key => {
      onTabChange(key);
    }}
    >
      {/* {JSON.stringify(tabList)} */}
      {contentList[activeTabKey]}
      <Row className={styles['ant-row-margin']} style={{position:"absolute",left:'87px',bottom:'10px'}}>
        <Col span={12}><Button onClick={cancel}>取消</Button></Col>
        <Col span={12}><Button onClick={submit} type="primary">确定</Button></Col>
      </Row>


    </Card>
  )
}
