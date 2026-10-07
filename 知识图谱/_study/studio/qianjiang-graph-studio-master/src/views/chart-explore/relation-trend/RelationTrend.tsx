import {Modal, Form, Button, Select, InputNumber, Input } from 'antd'
import { useEffect, useState, useRef} from 'react'
import { EdgeTypeSingleItem } from '@/views/chart-explore/form-items/FormItems'
// import RelationTrendGraph from '@/views/chart-explore/relation-trend-graph/RelationTrendGraph'
import {relationTrendGraphApi} from '@/api/chart'
import {dealNodes, dealEdges} from '@/utils/index'

const { Option } = Select;

type props = {
  visible:boolean,
  edgeTypeNameList: any,
  historyData: any,
  showRelationTrend: (flag: boolean) => void,
  mergeData: (data: any) => void,
  setLayoutType: (data: any, rankdir:string) => void
}

// 复杂查询-结果展示
const RelationTrend:React.FC<props> = ({ visible, edgeTypeNameList, historyData, showRelationTrend, mergeData, setLayoutType }) => {
  const [form] = Form.useForm();
  // const [graphVisible, setGraphVisible] = useState(false)
  // const [graphData, setGraphData] = useState<any>({nodes: [], edges: []})
  const expandRef = useRef<any>(null)
  const [taskName,setTaskName] = useState("")
  const [loading, setLoading] = useState<boolean>(false)

  // 录入历史记录参数
  useEffect(() => {
    if (historyData && historyData.type === 'flowDirection') {
      const param = JSON.parse(historyData.param)
      const { edgeTypeName, strengthCondition, taskName } = param
      form.setFieldsValue({
        taskName,
        edgeTypeName,
        strengthCondition
      })
    }
  }, [historyData, form])
  useEffect(() => {
    if(historyData)return
    if (visible) {
      let time="关系净流向-"+(new Date()).toLocaleString()
      setTaskName(time)
      form.setFieldsValue({taskName:time})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible])
  const onFinish = (param:any) => {
    setLoading(true)
    relationTrendGraphApi(param).then((res: any) => {
      showRelationTrend(false)
      const {vertexList, edgeList} = res.data
      const nodes = dealNodes(vertexList)
      const edges = dealEdges(edgeList)
      // setGraphData({nodes, edges})
      mergeData({nodes, edges})
      setLayoutType('流程图布局', 'LR')
      // setGraphVisible(true)
    }).finally(() => {
      setLoading(false)
    })
  }

  // const showGraph = (flag: boolean) => {
  //   setGraphVisible(flag)
  // }

  // 筛选强度
  const changeStrengthType = (value: string) => {
    expandRef.current.validateFields([['strengthCondition', 'value']])
  }
  const changeStrengthValue = () => {
    expandRef.current.validateFields([['strengthCondition', 'conditionType']])
  }

  return(
  <>
    <Modal
    title="关系净流向"
    visible={visible}
    className="relation-trend"
    maskClosable={false}
    footer={null}
    onCancel={() => {showRelationTrend(false)}}
    >
      <Form
        layout="vertical"
        onFinish={onFinish}
        form={form}
        ref={expandRef}
      >
        <Form.Item
          label="任务名称"
          name="taskName"
          rules={[{ required: true, message: '请输入任务名称' }]}
          initialValue={taskName}
          style={{marginBottom: "20px"}}
        >
          <Input placeholder="请输入任务名称" />
        </Form.Item>
        {/* 边类型 */}
        <EdgeTypeSingleItem edgeTypeNameList={edgeTypeNameList} />
        <Form.Item label="强度" style={{marginBottom: "0px"}}>
          <div style={{display: 'flex'}}>
            <Form.Item
            name={['strengthCondition', 'conditionType']}
            style={{width: '50%',marginRight: '10px'}}
            rules={[({ getFieldValue }) => ({
              validator(_, value) {
                const conditionValue = getFieldValue(['strengthCondition', 'value'])
                if ((conditionValue||conditionValue===0)&&!value) {
                  return Promise.reject(new Error('请选择!'));
                }
                return Promise.resolve()
              }
            })]}
            >
              <Select
              placeholder="请选择"
              allowClear={true}
              onChange={changeStrengthType}
              >
                <Option value="大于">大于</Option>
                <Option value="小于">小于</Option>
              </Select>
            </Form.Item>
            <Form.Item
            name={['strengthCondition', 'value']}
            style={{width: '50%'}}
            rules={[({ getFieldValue }) => ({
              validator(_, value) {
                if (getFieldValue(['strengthCondition', 'conditionType'])&&(value===undefined||value===null)) {
                  return Promise.reject(new Error('请选择!'));
                }
                return Promise.resolve()
              }
            })]}
            >
              <InputNumber min={0} placeholder="请输入"  style={{width: '100%'}} onChange={changeStrengthValue}/>
            </Form.Item>
          </div>
        </Form.Item>
        <Form.Item style={{'textAlign': 'center'}} >
          <Button type="primary" htmlType="submit" loading={loading}>查找</Button>
          { loading&&<div style={{color:"red", textAlign:'center', fontSize:"12px", marginTop:"6px"}}>若当前功能运行时间较长，可关闭页面，后续进入分析记录页面查看运行结果</div>}
        </Form.Item>
      </Form>
    </Modal>
    {/* {graphVisible
    &&
    <RelationTrendGraph
    visible={graphVisible}
    graphData={graphData}
    showGraph={showGraph}/>} */}
  </>
  )
}
export default RelationTrend