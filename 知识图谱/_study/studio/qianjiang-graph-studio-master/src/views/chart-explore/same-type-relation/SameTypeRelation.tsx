import React, { useEffect, useState } from 'react'
import { Modal, Form, InputNumber, Select, message, Row, Col, Input, Button } from 'antd'
import { dealNodes, dealEdges} from '@/utils'
import { querySameTypeRelationApi, findNodeApi, findById } from '@/api/chart'
import { DebounceSelect } from '@/components'
import store from '@/redux/store'
import  './SameTypeRelation.scss'

type Props = {
  mergeData:(data: any) => void,
  visible: boolean,
  historyData: any,
  showSameTypeRelation: (flag:boolean) => void
}
// 同类节点关系弹窗
const SameTypeReltion: React.FC<Props> = ({mergeData,visible,historyData, showSameTypeRelation}:Props) => {
  const [form] = Form.useForm();
  const [options,setOptions] = useState([])
  const [taskName,setTaskName] = useState("")
  const [confirmLoading,setConfirmLoading] = useState(false)
  const [vertexlist, setVertexList] = useState<any>([])

  // 录入历史记录参数
  useEffect(() => {
    if (historyData && historyData.type === 'searchSameTagRelation') {
      const param = JSON.parse(historyData.param)
      const { maxStep, tagName, taskName, vertexIdList } = param
      let list=[]
      vertexIdList?.forEach((item: any)=> {
        let param={} as any
        if (item.id) {
          param={id:item.id}
        }else{
          param={id:item}
        }
        let name, iconPath
        findById(param).then((res: any) => {
          name=res.data.name
          iconPath='/api/file'+res.data.tag.iconPath
          list.push({name,iconPath,key:param.id})
        }).then((res:any)=>{
          if (list.length===vertexIdList.length) {
            const vertexIdListData = list?.map((list: any)=> {
             const {name,iconPath,key}=list
                return {
                  key:key,
                  label: (
                    <div  style={{display:"flex",alignItems: "center"}}>
                    {iconPath&&
                    <img style={{width:"15px",height:"15px",marginRight: "5px"}} src={iconPath} alt="" />
                    }
                    <span>{name}</span>
                  </div>
                  )
                }
            })
            setVertexList(vertexIdListData)
            form.setFieldsValue({
              maxStep,
              tagName,
              taskName,
              vertexIdList
            })
          }
        })
      });

    }
  }, [historyData, form])

  useEffect(() => {
    const tmp:any = []
    store.getState().nodeType.forEach((item:any) => {
      tmp.push({value:item.name})
    })
    setOptions(tmp)
    if (visible) {
      let time="同类型节点关系-"+(new Date()).toLocaleString()
      setTaskName(time)
      form.setFieldsValue({taskName:time})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[visible])


  const TaskNameChange = (nv:any) => {
    setTaskName(nv.target.value)
  }

  const findNode = (value: string) => {
    return new Promise(((resolve, reject) => {
      findNodeApi({keyword: value}).then((res: any) => {
        const iconPathList: any = store.getState().iconPathList
        resolve(res.data.map((it: any) => ({
          label: (
            <div  style={{display:"flex",alignItems: "center"}}>
              {iconPathList[it.tag.name]&&
              <img style={{width:"15px",height:"15px",marginRight: "5px"}} src={iconPathList[it.tag.name]} alt="" />
              }
              <span>{it.name}</span>
            </div>
          ),
          value: it.id
        })))
      }).catch(e => {

      })
    }))
  }
  const handleChange = (values: any) => {
    setVertexList(values)
  }
  const handleOk = (param) => {
    const { tagName, maxStep } = param

    if (!tagName || !maxStep) {
      message.warn('请输入选项')
      return
    }
    if (!taskName) {
      message.warn('请输入任务名称')
      return
    }
    param.vertexIdList=vertexlist?.map(item=>item.key)
    setConfirmLoading(true)
    querySameTypeRelationApi(param)
    .then(res => {
      let nodes = dealNodes(res.data.vertexList)
      let edges = dealEdges(res.data.edgeList)
      mergeData({
        // highlight: false,
        edges,
        nodes
      })
    })
    .finally(() => {
      form.resetFields();
      setConfirmLoading(false)
      setVertexList([])
      setTaskName("")
      closeModal()
    })
  }

  const closeModal = () => {
    showSameTypeRelation(false)
  }

  return(
    <Modal
      className='sameTypeRelation'
      width={640}
      title="同类型节点关系"
      visible={visible}
      footer={null}
      style={{overflow:'hidden'}}
      maskClosable={false}
      >
      <Form
        form={form}
        onFinish={handleOk}
        initialValues={{
          vertexIdList:"",
          tagName: "",
          maxStep: 1,
          taskName:taskName,
        }}
        layout="vertical"
        wrapperCol={{ flex: 1 }}
      >
      <Form.Item
        label="任务名称"
        name="taskName"
      >
        <Input onChange={TaskNameChange} />
      </Form.Item>
      <Row gutter={30}>
        <Col span={12}>
          <Form.Item label="实体类型" name="tagName">
            <Select
              placeholder="请选择"
              allowClear
              options={options}
            />
          </Form.Item>
        </Col>
        <Col span={12}>
        <Form.Item label="步数" name="maxStep">
          <InputNumber
            style={{ width: "100%" }}
            min={1}
            placeholder="请输入正整数" />
        </Form.Item>
        </Col>
      </Row>
      <Form.Item
        label="需要包含的实体（默认为全部指定类型的实体)"
        name="vertexIdList">
        <DebounceSelect
          mode="multiple"
          placeholder="请输入关键字"
          allowClear={true}
          fetchOptions={findNode}
          vertexlist={vertexlist}
          onChange={handleChange}
        />
      </Form.Item>
      <Form.Item wrapperCol={{ offset: 8, span: 16 }}>
          <Button size='middle' onClick={closeModal}>取消</Button>
          <Button size='middle' type="primary" htmlType="submit" style={{margin: '0 20px'}} loading={confirmLoading}>查找</Button>
      </Form.Item>
    </Form>
    { confirmLoading &&<div style={{color:"red", textAlign:'center', fontSize:"12px", marginTop:"6px"}}>若当前功能运行时间较长，可关闭页面，后续进入分析记录页面查看运行结果</div>}
    </Modal>
  )
}
export default SameTypeReltion