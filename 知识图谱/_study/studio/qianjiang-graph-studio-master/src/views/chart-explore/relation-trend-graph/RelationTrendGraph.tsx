import { Modal, Col, Row, Statistic } from 'antd'
import { useEffect, useRef, useState } from 'react'
import './RelationTrendGraph.scss'
import Graphin from '@antv/graphin'
import {Legend} from '@antv/graphin-components'
import {IG6GraphEvent} from "@antv/g6";

const defaultLayout = {
  type: 'dagre',
  rankdir: 'LR',
}

// 不同选中样式
const defaultStatusStyle = {
  status: {
    selected: {
      keyshape: {
        stroke: '#40a9ff'
      }
    }
  }
}
// node默认样式
const defaultNodeStyle: { [key: string]: any } = {
  style: {
    keyshape: {
      size: 30,
      fill: 'transparent',
      stroke: 'gray',
      lineWidth: 1,
      opacity: 1,
      fillOpacity: 1
    }
  }
}
// edge默认样式
const defaultEdgeStyle: { [key: string]: any } = {
  style: {
    keyshape: {
      stroke: 'gray',
      lineWidth: 1,
      opacity: 1
    }
  }
}
type props = {
  visible:boolean,
  graphData: any,
  showGraph: (flag: boolean) => void,
}
// 复杂查询-结果展示
const RelationTrendGraph:React.FC<props> = ({visible, graphData, showGraph}) => {
  const graphinTrendRef = useRef<Graphin>(null)
  const [data, setData] = useState<any>({nodes: [], edges: []})

  const getNodeStyle = (node: any) => {
    return {
      ...defaultNodeStyle, ...node.style?.keyshape,
      stroke: ['true', true].includes(node.attrs.suspectFlag) ? 'red' : 'gray'
    }
  }
  
  useEffect(() => {
    const graph = graphinTrendRef.current!.graph
    graphData.nodes.forEach((it: any, index: number) => {
      it.style = {
        label: it.style?.label,
        keyshape: getNodeStyle(it),
        icon: {
          type: 'image',
          value: it.iconPath,
          size: [20, 20],
        }
      }
      it.attrs.suspectFlag = ['true', true].includes(it.attrs.suspectFlag) ? '嫌疑人' : '其他'
    })
    setData(graphData)
    graph.render();
    setTimeout(() => {
      graph.fitCenter()
    }, 100)
    const handleClick = (evt: IG6GraphEvent) => {
      if (evt.item !== null && evt.item.getType() === 'edge') {
        graph.setItemState(evt.item!, 'selected', true)
      }
    }
    graph.on('edge:click', handleClick)
  }, [graphData])
 
  return(
  <>
    <Modal 
    visible={visible} 
    className="relation-trend-graph"
    maskClosable={false}
    footer={null}
    width={'auto'}
    onCancel={() => {showGraph(false)}}
    >
      <Graphin data={data}  
        layout={defaultLayout} 
        ref={graphinTrendRef}
        defaultNode={defaultNodeStyle}
        defaultEdge={defaultEdgeStyle}
        nodeStateStyles={defaultStatusStyle}
        edgeStateStyles={defaultStatusStyle}
        >
        <Legend bindType="node" sortKey="attrs.suspectFlag" colorKey="style.keyshape.stroke" style={{right: 30}}>
          <Legend.Node/>
        </Legend>
      </Graphin>
      <div style={{position: 'fixed',left: '50px',bottom:0}}>
        <Row gutter={16}>
          <Col span={12}>
            <Statistic title="Node " value={data?.nodes.length} />
          </Col>
          <Col span={12}>
            <Statistic title="Edge" value={data?.edges?.length} />
          </Col>
        </Row>
      </div>
    </Modal>
  </>
  ) 
}
export default RelationTrendGraph