import SvgIcon from '@/components/svg-icon/SvgIcon'
import {Modal, Tabs, Select, Form, Button, Input, DatePicker, InputNumber, message} from 'antd'
import { useEffect, useState, useRef, useCallback } from 'react'
import  './ComplicatedSearch.scss'
import store from '@/redux/store'
import { dealSelectData, getSorting } from '@/utils/index'
import { stringProperty, numberProperty, initProperty } from '@/common'
import {complexSearchApi} from '@/api/chart'
import moment from 'moment'
import { StrengthCount } from '@/views/chart-explore/form-items/FormItems'
import ShowResult from '@/views/chart-explore/show-result/ShowResult'


const {TabPane} = Tabs
const { Option } = Select
const { RangePicker } = DatePicker;

type props = {
  visible:boolean,
  historyData: any,
  openModal: (flag: boolean) => void
  mergeData: (data: any) => void
}
const totalDataInit = {
  'vertexParam0': {
    propertyList: [], // 属性下拉
    tagName: '', // 类型值
    propertyValue: '', // 属性值
    conditionTypeList: '', // 属性-类型下拉
    conditionType: '', // 属性-类型值
    propertyInputValue: ''
  },
  'vertexParam': {
    propertyList: [],
    tagName: '',
    propertyValue: '',
    conditionTypeList: '',
    conditionType: '',
    propertyInputValue: ''
  },
  'edgeParam': {
    propertyList: [],
    tagName: '',
    strengthType: '',
    strengthValue: '',
    countType: '',
    countValue: '',
    conditionTypeList: '',
    conditionType: ''
  },
  'vertexParam2': {
    propertyList: [],
    tagName: '',
    propertyValue: '',
    conditionTypeList: '',
    conditionType: '',
    propertyInputValue: ''
  }
}

const ComplicatedSearch:React.FC<props> = ({ visible, historyData, openModal, mergeData }) => {
  // 不同实体对应不同表单dom
  const [tabType, setTabType] = useState<string>('1')
  // 实体类型
  const [nodeType, setNodeType] = useState([])
  const [nodeTypeList, setNodeTypeList] = useState([])
  // 边类型
  const [edgeType, setEdgeType] = useState([])
  const [edgeTypeList, setEdgeTypeList] = useState([])
  const [totalData, setTotalData] = useState<any>(totalDataInit)
  const [form1] = Form.useForm()
  const [form2] = Form.useForm()

  const [resultVisible, setResultVisible] = useState(false)
  // 查询到的图数据
  const [graphData, setGraphData] = useState<any>({vertexList: [], edgeList: []})
  const expandRef = useRef<any>(null)
  const [loading, setLoading] = useState(false)
  
  // 根据历史记录获取表单初始话数据
  const getInitData = useCallback((vertexParam: any, data: any, type:string) => {
    const tagName = vertexParam.tagName
    if (tagName) {
      let nodeData: any = nodeTypeList.filter((item: any) => item.name === tagName)
      let propertyInfoList = dealSelectData(nodeData[0].propertyInfoList, 'key' , 'key')
      data[type].propertyList = [...initProperty, ...propertyInfoList]
      const statisticsType = vertexParam.statisticsCondition?.statisticsType || vertexParam.propertyCondition?.key
      if (statisticsType) {
        let propertyType = data[type].propertyList
                .filter((item: any) => item.value === statisticsType)
                .map((item: any) => item.propertyType)[0]
        data[type].conditionTypeList = propertyType === 'string' ? stringProperty : numberProperty
      
      } else {
        data[type].conditionTypeList = []
      }
      setTotalData(data)
    }
  },[nodeTypeList])

  const dealStatisticsCondition = (vertexParam: any) => {
    const propertyCondition = vertexParam?.propertyCondition
    if (propertyCondition?.key) {
      vertexParam.statisticsCondition = {
        ...propertyCondition,
        statisticsType: propertyCondition.key
      }
      delete vertexParam.statisticsCondition.key
      delete vertexParam.propertyCondition
    }
  }

  // 录入历史记录参数
  useEffect(() => {
    if (historyData && historyData?.type === 'complex') {
      const param = JSON.parse(historyData.param)
      const {mode,
         edgeParam, 
         vertexParam, 
         vertexParam2} = param
      const data: any = totalDataInit
      dealStatisticsCondition(vertexParam)
      dealStatisticsCondition(vertexParam2)
      if (mode === 'vertex') {
        setTabType('1')
        getInitData(vertexParam, data,'vertexParam0')
        
        form1.setFieldsValue({
          vertexParam0: {...vertexParam}
        })
      } else {
        setTabType('nodea')
        const {conditionType, key, value, value2} = edgeParam.propertyCondition
        
        // 初始化实体A  初始化实体B
        getInitData(vertexParam, data,'vertexParam')
        getInitData(vertexParam2, data,'vertexParam2')
        // 历史记录 初始化连接
        const edgeTypeName = edgeParam.edgeTypeName
        if (edgeTypeName) {
          let nodeData: any = edgeTypeList.filter((item: any) => item.name === edgeTypeName)
          let propertyInfoList = dealSelectData(nodeData[0].propertyInfoList, 'key' , 'key')
          data.edgeParam.propertyList = [...initProperty, ...propertyInfoList]
          const statisticsType = edgeParam.propertyCondition.key
          if (statisticsType) {
            let propertyType = data.edgeParam.propertyList
                    .filter((item: any) => item.value === statisticsType)
                    .map((item: any) => item.propertyType)[0]
          data.edgeParam.conditionTypeList = propertyType === 'string' ? stringProperty : numberProperty
          } else {
            data.edgeParam.conditionTypeList = []
          }
        }
        setTotalData(data)
        // 时间格式化GMT
        const timeCondition = edgeParam.timeCondition
        let startTime, endTime
        if (timeCondition) {
          startTime = moment(timeCondition.value)
          endTime = moment(timeCondition.value2)
        }
        
        form2.setFieldsValue({
          vertexParam,
          vertexParam2,
          edgeParam: {
            ...edgeParam,
            statisticsCondition: {
              conditionType,
              statisticsType: key,
              value,
              value2
            },
            timeCondition: [startTime, endTime]
          }
        })
      }      
    }
  }, [historyData, form1, form2, edgeTypeList, getInitData])

  const closeModal = () => {
    openModal(false)
  }
  const dealPropertyCondition = (params: any) => {
    let values = initProperty.map((item: any) => item.value)
    Object.keys(params).forEach((key: string) => {
      let value = params[key]?.statisticsCondition?.statisticsType
      if (!values.includes(value)) {
        params[key].propertyCondition = {
          ...params[key].statisticsCondition,
          key: value
        }
        delete params[key].propertyCondition.statisticsType
        delete params[key].statisticsCondition
      }
    })
    return params
  }

  // 全库搜索
  const onFinish = () => {
    let params: any
    // 实体
    if (tabType === '1') {
      const formData = form1.getFieldsValue()
      dealPropertyCondition(formData)
      let vertexParam = formData.vertexParam0
      const { statisticsCondition } = vertexParam
      if (statisticsCondition) {
        let {statisticsType, edgeTypeName} = statisticsCondition
        let sorting1 = getSorting(statisticsCondition, statisticsType, edgeTypeName)
        if (sorting1) {
          vertexParam.sorting = sorting1
        }
      }
      params = {
        vertexParam,
        mode: 'vertex'
      }
    } else { // 实体连接
      params = form2.getFieldsValue()
      dealPropertyCondition(params)
      if (!params.edgeParam.edgeTypeName) {
        message.warning('请选择连接类型')
        return
      }
      setLoading(true)
      
      if (params.edgeParam?.timeCondition?.length) {
        let timeCondition = params.edgeParam.timeCondition
        let value = moment(timeCondition[0]).format('YYYY-MM-DD HH:mm:ss')
        let value2 = moment(timeCondition[1]).format('YYYY-MM-DD HH:mm:ss')
        params.edgeParam.timeCondition = {value, value2}
      }
      
      let {strengthCondition, countCondition} = params.edgeParam
      
      let sorting = getSorting(strengthCondition, 'strength') || getSorting(countCondition, 'count')
      
      
      if (sorting) {
        params.edgeParam.sorting = sorting
      }
      let {vertexParam, vertexParam2} = params
      if (vertexParam.statisticsCondition) {
        let {statisticsType, edgeTypeName} = vertexParam.statisticsCondition
        let sorting1 = getSorting(vertexParam.statisticsCondition, statisticsType, edgeTypeName)
        if (sorting1) {
          params.vertexParam.sorting = sorting1
        }
      }
      if (vertexParam2.statisticsCondition) {
        let {statisticsType, edgeTypeName} = vertexParam2.statisticsCondition
        let sorting2 = getSorting(vertexParam2.statisticsCondition, statisticsType, edgeTypeName)
        if (sorting2) {
          params.vertexParam2.sorting = sorting2
        }
      }
      params.mode = 'all'
    }
    complexSearchApi(params).then(res => {
      setGraphData(res.data)
    }).finally(() => {
      // 关闭复杂连接窗口，
      openModal(false)
      // 打开结果展示窗口
      setResultVisible(true)
      setLoading(false)
    })
  }
  // 实体跟实体连接tab切换
  const onChange = (key: string) => {
    if (key === '1') {
      setTabType(key)
    } else {
      setTabType('nodea')
    }
  }

  // 筛选强度
  const changeStrength = (value: string) => {
    expandRef.current.validateFields([['edgeParam', 'strengthCondition', 'value']])
    setTotalData({
      ...totalData,
      edgeParam: {
        ...totalData.edgeParam,
        strengthType: value
      }
    })
  }
  const changeStrengthValue = () => {
    expandRef.current.validateFields([['edgeParam', 'strengthCondition', 'conditionType']])
  }
  // 筛选次数
  const changeCount = (value: string) => {
    expandRef.current.validateFields([['edgeParam', 'countCondition', 'value']])
    setTotalData({
      ...totalData,
      edgeParam: {
        ...totalData.edgeParam,
        countType: value
      }
    })
  }
  const changeCountValue = () => {
    expandRef.current.validateFields([['edgeParam', 'countCondition', 'conditionType']])
  }
  useEffect(() => {
    // 监听node类型 边类型
    let unsubscribe = store.subscribe(() => {
      if (store.getState().nodeType) {
        let nodeType = dealSelectData(store.getState().nodeType, 'id', 'name')
        setNodeType(nodeType)
        setNodeTypeList(store.getState().nodeType)
      }
      if (store.getState().edgeType) {
        let edgeType = dealSelectData(store.getState().edgeType, 'id', 'name')
        setEdgeType(edgeType)
        setEdgeTypeList(store.getState().edgeType)
      }
    })
    return () => {unsubscribe()}
  }, [])
  
  // 点击实体  连接图标
  const iconClick = (type1:string) => {
    type1 !== tabType && setTabType(type1)
  }
  // 类型下拉
  const TypeItem = (props: any) => {
    return(
    <Form.Item
      label="类型"
      name={props.name}
      style={{minWidth: '220px', marginRight: '10px'}}
    >
      <Select 
      showSearch
      allowClear={true} 
      placeholder="请选择" 
      onChange={(value: string) => {props.changeType(value)}}
      >
        {
          tabType==='line' ? edgeType.map((item: any) => (
            <Option value={item.label} key={item.value}>{item.label}</Option>  
          ))
          : nodeType.map((item: any) => (
            <Option value={item.label} key={item.value}>{item.label}</Option>  
          ))
        }
      </Select>
    </Form.Item>)
  }

  // 弹框底部button按钮
  const buttonItem = (
    <Form.Item>
      <Button onClick={closeModal}>取消</Button>
      <Button type="primary" htmlType="submit" style={{margin: '0 20px'}} loading={loading}>全库搜索</Button>
      {/* <Button type="primary">图上查询</Button> */}
    </Form.Item>
  )

  // 属性
  const PropertyItem = (props: any) => {
    let type = props.type
    let form = tabType === '1' ? form1 : form2
    // 筛选属性
    const changeProperty = (value: string) => {
      let flag = initProperty.some((item: any) => item.value === value)
      let data = JSON.parse(JSON.stringify(totalData))
      data[type].propertyValue = flag
      // 属性改变，对应的类型
      let propertyType = totalData[type].propertyList
                  .filter((item: any) => item.value === value)
                  .map((item: any) => item.propertyType)[0]
      if (value) {
        data[type].conditionTypeList = propertyType === 'string' ? stringProperty : numberProperty
      } else {
        data[type].conditionTypeList = []
      }
      setTotalData(data)
      // 属性改变 conditionType默认值重置
      form.setFieldsValue({
        [type]: {
          statisticsCondition: {conditionType: '', value: ''}
        }
      })
    }
    const changeConditionType = (value: string) => {
      let data = JSON.parse(JSON.stringify(totalData))
      data[type].conditionType = value
      setTotalData(data)
    }

    return (
      <Form.Item
      label="属性"
      style={{minWidth: '520px',marginBottom: '0'}}
      >
        <Form.Item
          name={[type, 'statisticsCondition', 'statisticsType']}
          style={{width: '110px',marginRight: '10px'}}
        >
          <Select 
          showSearch
          allowClear={true} 
          placeholder="请选择" 
          onChange={changeProperty}
          >
            {
              totalData[type].propertyList?.filter((item: any) => ['suspectFlag', 'suspectFlag1'].indexOf(item.label) === -1)
              .map((item: any) => (
                <Option value={item.value} key={item.value}>{item.label}</Option>  
              ))
            }
          </Select>
        </Form.Item>
        {
        initProperty.some((item: any) => item.value === form.getFieldValue([type, 'statisticsCondition', 'statisticsType'])) && 
        <Form.Item
          name={[type, 'statisticsCondition', 'edgeTypeName']}
          style={{width: '110px',marginRight: '10px'}}
        >
          <Select showSearch allowClear={true} placeholder="请选择">
          {
              edgeType.map((item: any) => (
                <Option value={item.label} key={item.value}>{item.label}</Option>  
              ))
            }
          </Select>
        </Form.Item>}
        <Form.Item
          name={[type, 'statisticsCondition', 'conditionType']}
          style={{width: '110px',marginRight: '10px'}}
        >
          <Select 
          showSearch
          allowClear={true} 
          placeholder="请选择" 
          options={totalData[type].conditionTypeList} 
          value={totalData[type].conditionType}
          onChange={changeConditionType}
          />
        </Form.Item>
        <Form.Item
          name={[type, 'statisticsCondition', 'value']}
          style={{width: '110px',marginRight: '10px'}}
        >
          {totalData[type].conditionTypeList[0]?.label === '包含'
          ? <Input placeholder='请输入' value={totalData[type].propertyInputValue}/>
          : <InputNumber min={1} value={totalData[type].propertyInputValue}/>}
        </Form.Item>
        {totalData[type].conditionType === '介于' && totalData[type].conditionTypeList[0]?.label !== '包含' &&
        <Form.Item
          name={[type, 'statisticsCondition', 'value2']}
          style={{width: '110px',marginRight: '10px'}}
        >
          <InputNumber min={1} />
        </Form.Item>}
      </Form.Item>
    )
  }

  const FormItem1 = (props: any) => {
    let type = props.type
    let form = tabType === '1' ? form1 : form2
    // 类型下拉修改，属性联动变化
    const changeType = (value: string) => {
      let data = JSON.parse(JSON.stringify(totalData))
      data[type].tagName = value
      // 类型改变，属性下拉框改变
      if (value) {
        let nodeData: any = nodeTypeList.filter((item: any) => item.name === value)
        let propertyInfoList = dealSelectData(nodeData[0].propertyInfoList, 'key' , 'key')
        data[type].propertyList = [...initProperty, ...propertyInfoList]
      }
      setTotalData(data)
      
      
      // 类型改变  属性值重置
      form.setFieldsValue({
      [type]: {
        tagName: value,
        statisticsCondition: {
          "statisticsType":"",
          "conditionType":"",
          "edgeTypeName":"",
          "value":"",
          "value2":""
        }}})
    }
    return(
    <>
      {/* 类型  属性 */}
      <TypeItem name={[type, 'tagName']} changeType={changeType}></TypeItem>
      <Form.Item
        label="标签"
        name={[type, 'nameKeyword']}
        style={{width: '220px'}}
        >
        <Input placeholder="请输入" />
      </Form.Item>{}
      {form.getFieldValue([type, 'tagName']) && <PropertyItem type={type}/>}
      </>)
  }
  const changeType = (value: string) => {
    let data = JSON.parse(JSON.stringify(totalData))
    data.edgeParam.tagName = value
    if(value){
      let edgeData: any = edgeTypeList.filter((item: any) => item.name === value)
      let propertyInfoList = dealSelectData(edgeData[0]?.propertyInfoList, 'key' , 'key')
      data.edgeParam.propertyList = propertyInfoList
    }
    setTotalData(data)
  }

  // 结果展示
  const showResult = (flag: boolean, str?: string) => {
    setResultVisible(flag)
    if (str) {
      mergeData({...graphData, highlight: true})
    }
  }
  
  return(
  <>
    <Modal 
    title="复杂查询" 
    visible={visible} 
    width={1068}
    className="complicated-search"
    footer={null}
    maskClosable={false}
    onCancel={closeModal}
    destroyOnClose={true}
    >
      <Tabs defaultActiveKey={tabType === '1' ? '1' : '2'} onChange={onChange}>
        <TabPane tab="实体" key="1" >
          <div className="node-icon">
            <div><SvgIcon iconClass='node-checked'/> </div>
            <div style={{color:'#5192f5'}}>实体A</div>
          </div>
        </TabPane>
        <TabPane tab="实体连接" key="2">
          <div onClick={() => {iconClick('nodea')}} className="node-icon icon">
            <div>{ tabType === 'nodea' ? <SvgIcon iconClass='node-checked'/> : <SvgIcon iconClass='node-unchecked'/> }</div>
            <div style={tabType === 'nodea'? {color:'#5192f5'}:{color: '#555555'}}>实体A</div>
          </div>
          <div 
            className={tabType === 'line' ? 'line line-checked' : 'line line-unchecked'} 
            onClick={() => {iconClick('line')}}>
            连接
          </div>
          <div onClick={() => {iconClick('nodeb')}} className="node-icon icon">
            <div>{ tabType === 'nodeb' ? <SvgIcon iconClass='node-checked'/> : <SvgIcon iconClass='node-unchecked'/> }</div>
            <div style={tabType === 'nodeb' ? {color:'#5192f5'}:{color: '#555555'}}>实体B</div>
          </div>
        </TabPane>
      </Tabs>
      {/* 实体对应表单 */}
      <Form 
      form={form1} 
      onFinish={onFinish} 
      style={{display: tabType === '1' ? 'block' : 'none'}}
      > 
        <div style={{display: 'flex',flexWrap: 'wrap'}}>
          <FormItem1 type="vertexParam0"></FormItem1>
        </div>
          {buttonItem}
      </Form>
      {/* 实体连接-实体A对应表单 */}
      <Form 
      style={{display: tabType === '1' ? 'none' : 'block'}} 
      form={form2}
      ref={expandRef}
      onFinish={onFinish} 
      >
        <div style={{display: tabType === 'nodea' ? 'flex' : 'none',flexWrap: 'wrap'}}>
          <FormItem1 type="vertexParam"></FormItem1>
        </div>
        {/* 实体连接-连接线对应表单 */}
        <div style={{display: tabType === 'line' ? 'flex' : 'none',flexWrap: 'wrap'}}>
          {/* 类型 */}
          <TypeItem name={['edgeParam', 'edgeTypeName']} changeType={changeType}></TypeItem>
          {/* 属性 */}
          {form2.getFieldValue(['edgeParam', 'edgeTypeName']) && <PropertyItem type="edgeParam"/>}
          {/* 强度 次数 */}
          <StrengthCount 
          type={["edgeParam"]}
          strengthType = {totalData?.edgeParam?.strengthType}
          countType = {totalData?.edgeParam?.countType}
          changeStrength={changeStrength} 
          changeCount={changeCount} 
          changeStrengthValue={changeStrengthValue}
          changeCountValue={changeCountValue}/>
          <Form.Item 
          label="时间" 
          name={['edgeParam', 'timeCondition']}>
            <RangePicker  format="YYYY/MM/DD HH:mm:ss"/>
          </Form.Item>
        </div>
        <div style={{display: tabType === 'nodeb' ? 'flex' : 'none',flexWrap: 'wrap'}}>
          <FormItem1 type="vertexParam2"></FormItem1>
        </div>
        {buttonItem}
      </Form>
      
      
      {/* 实体连接-实体B对应表单 */}
      
    </Modal>
    {/* 复杂查询-结果展示 */}
    <ShowResult visible={resultVisible} openModal={showResult} graphData={graphData} mergeData={mergeData} tabType={tabType}/>
  </>
  ) 
}
export default ComplicatedSearch