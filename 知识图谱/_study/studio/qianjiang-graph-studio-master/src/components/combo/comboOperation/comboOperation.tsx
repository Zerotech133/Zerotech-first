import React,{useEffect,useState} from "react"
import { Modal,Select,Form,Button, message,Tabs} from "antd"
import {GraphinContext} from '@antv/graphin'
const { Option } = Select;
const { TabPane } = Tabs
type props = {
  visible: boolean,
  setVisible: (visible: boolean) => void,
  data:any,
  combosList:any
}
export const ComboOperation: React.FC<props> = ({visible,setVisible,combosList,data}) => {
  const [selectcombos, setSelectcombos] = useState<string[]>([])
  const graph = React.useContext(GraphinContext).graph
  const [formComboOperation] = Form.useForm();
  const [formSelectCombo] = Form.useForm();
 
  let arr:any=[]
  useEffect(()=>{
    let allCombo:any=[]
    data.nodes.forEach((item:any)=>{
      if(item.comboId)allCombo.push(...item.comboId)
    })
    setSelectcombos([])
  },[visible,data]) 
  // 关闭弹窗
  const handleComboOperationClose = () => {
    setVisible(false)
    formComboOperation.resetFields();
    formSelectCombo.resetFields();
  }
  // 高亮节点和边
  const handleChange = (value: string[]) => {
    setSelectcombos(value)
  };
  const highlightNodeId=(ids:any)=>{
    const nodes = graph.getNodes();
    const edges=graph.getEdges()
    nodes.forEach((it:any)=>{
      graph.clearItemStates(it);
      let id =it.getModel().id
      let isHighlight=ids.find((combo:any)=>id===combo)
      if (isHighlight!==undefined) {
        graph.setItemState(it, 'actived', true)
      }else{
        graph.setItemState(it, 'disabled', true)
      }
    })
    edges.forEach(it => {
      graph.clearItemStates(it);
      const model = it.getModel()
      let source=ids.find((id:any)=>id===model.source)
      let target=ids.find((id:any)=>id===model.target)
      if (source!==undefined&&target!==undefined) {
        graph.setItemState(it, 'actived', true)
      }else{
        graph.setItemState(it, 'disabled', true)
      }
    })
  }
  // 集合计算
  const onFinish=(value: any)=>{
    const nodes = graph.getNodes();
    const edges=graph.getEdges()
    value.combosNmae.forEach((item:any,index:any)=>{
      let record:any=[]
      data.nodes.forEach((node:any) => {
        let isInclude=node.comboId?.includes(item)
        if(isInclude){
          record.push(node.id)
        }
      })
      arr.push(record)
    });
    let result:any
    if (value.operationType==='intersection') {//交
        result = arr.reduce((previousValue:any, currentValue:any) => {
        return previousValue.filter((item:any) => currentValue.includes(item))
      })
    }else if (value.operationType==='union'){//并
      let noRepeatCombo:any=new Set(arr.flat())
        result=[...noRepeatCombo]
    }else if (value.operationType==='complement'){//补
      let intersection=arr.reduce((previousValue:any, currentValue:any) => {
        return previousValue.filter((item:any) => currentValue.includes(item))
      })
      result=[]
      data.nodes.forEach((node:any)=>{
        let intersectionNode=intersection.find((item:any)=>item===node.id)
        if(intersectionNode===undefined)result.push(node.id)
      })
    }
    if (result.length>0) {
      highlightNodeId(result)
    }else{
      message.warn('不存在相关节点')
      nodes.forEach((it:any)=>{
        graph.clearItemStates(it);
      })
      edges.forEach(it => {
        graph.clearItemStates(it);
      })
    }
    setVisible(false)
    formComboOperation.resetFields();
    formSelectCombo.resetFields();
  }
  // 元素突出
  const onFinishSelectCombo=(value:any)=>{
    let result:any=[]
    value.selectCombo.forEach((item:any)=>{
      data.nodes.forEach((node:any) => {
        let isInclude=node.comboId?.includes(item)
        if(isInclude){
          result.push(node.id)
        }
      })
    });
    highlightNodeId(result)
    formComboOperation.resetFields();
    formSelectCombo.resetFields();
    setVisible(false)
  }
  let OperationType:any=[
    {
      type:'intersection',
      label:'集合交集'
    },
    {
      type:'union',
      label:'集合并集'
    },
    {
      type:'complement',
      label:'集合补集'
    },
  ]
  return (
    <>
      <Modal 
        title="集合操作"
        className="combos"
        visible={visible}
        footer={null}
        width={400}
        centered={true}
        bodyStyle={{ padding: '0px 24px'}}
        onCancel={handleComboOperationClose}
      >
        <Tabs defaultActiveKey="ComboOperation">
          <TabPane tab="集合计算" key="ComboOperation" forceRender>
            <Form onFinish={onFinish} form={formComboOperation}>
                <Form.Item
                label="计算类型"
                name="operationType"
                rules={[{ required: true, message: '请选择计算类型' }]}
              >
                <Select allowClear={true} placeholder="请选择计算类型">
                    {
                      OperationType.map((item:any)=>{
                        return (
                          <Option value={item.type} key={item.type}>{item.label}</Option>
                        )
                      })
                    }
                </Select>
              </Form.Item>
              <Form.Item
                className='redStart'
                label="集合列表"
                name="combosNmae"
                rules={[({ getFieldValue }) => ({
                  validator(_, value) {
                      if (selectcombos?.length > 1) {
                        return Promise.resolve()
                      }
                      return Promise.reject(new Error('请至少选择2个点列表!'));
                    }
                  })]}
              >
                <Select
                  mode="multiple"
                  style={{ width: '100%' }}
                  placeholder="请选择集合"
                  optionLabelProp="label"
                  onChange={handleChange}
                  value={selectcombos}
                >
                  {
                    combosList.map((item:any)=>(
                      <Option value={item} label={item} key={item}>
                        <div className="demo-option-label-item">
                          {item}
                        </div>
                      </Option>
                    ))
                  }
                </Select>
              </Form.Item>
              <Form.Item wrapperCol={{ offset: 8, span: 16 }}>
                <Button size='middle' onClick={handleComboOperationClose}>取消</Button>
                <Button size='middle' type="primary" htmlType="submit" style={{ margin: '0 20px' }}>确认</Button>
              </Form.Item>
            </Form>
          </TabPane>
          <TabPane tab="元素突出" key="SelectCombo" forceRender>
            <Form onFinish={onFinishSelectCombo} form={formSelectCombo}>
              <Form.Item
                label="集合列表"
                name="selectCombo"
                rules={[{ required: true, message: '至少选中一个集合' }]}
              >
                <Select
                  mode="multiple"
                  style={{ width: '100%' }}
                  placeholder="请选择集合"
                  optionLabelProp="label"
                  value={selectcombos}
                >
                  {
                    combosList.map((item:any)=>(
                      <Option value={item} label={item} key={item}>
                        <div className="demo-option-label-item">
                          {item}
                        </div>
                      </Option>
                    ))
                  }
                </Select>
              </Form.Item>
              <Form.Item wrapperCol={{ offset: 8, span: 16 }}>
                <Button size='middle' onClick={handleComboOperationClose}>取消</Button>
                <Button size='middle' type="primary" htmlType="submit" style={{ margin: '0 20px' }}>确认</Button>
              </Form.Item>
            </Form>
          </TabPane>
        </Tabs>   
      </Modal>
    </>
  )
}
    