import {useState} from 'react'
import { Form, Select, InputNumber } from 'antd'
import { strengths } from '@/common'
import './FormItems.scss'

const { Option } = Select

// 强度 次数表单
export const StrengthCount = (props: any) => {
  let { type, countType, strengthType } = props

  const changeStrength = (value: string) => {
    props.changeStrength(value)
  }
  const changeStrengthValue = () => {
    props.changeStrengthValue()
  }
  const changeCount = (value: string) => {
    props.changeCount(value)
  }
  const changeCountValue = () => {
    props.changeCountValue()
  }
  
  return (
    <>
      <Form.Item
          label="强度"
          className='strength-item'
          style={{marginBottom: "0px"}}
          >
          <Form.Item
            name={[...type, 'strengthCondition', 'conditionType']}
            style={{marginRight: '10px', width: '110px'}}
            rules={[({ getFieldValue }) => ({
              validator(_, value) {
                if (getFieldValue([...type, 'strengthCondition', 'value'])&&!value) {
                  return Promise.reject(new Error('请选择!'));
                }
                return Promise.resolve()
              }
            })]}
          >
            <Select 
            placeholder="请选择" 
            allowClear={true} 
            onChange={changeStrength}>
            {
              strengths.map((it: any) => (
                <Option key={it.value} value={it.value} disabled={(['最大', '最小'].includes(countType)) && (['最大', '最小'].includes(it.value))}>{it.label}</Option>
              ))
            }
            </Select>
          </Form.Item>
          <Form.Item
            name={[...type, 'strengthCondition', 'value']}
            style={{marginRight: '10px', width: '110px'}}
            rules={[({ getFieldValue }) => ({
              validator(_, value) {
                if (getFieldValue([...type, 'strengthCondition', 'conditionType'])&&!value) {
                  return Promise.reject(new Error('请输入!'));
                }
                return Promise.resolve()
              }
            })]}
            >
            <InputNumber min={1} placeholder="请输入" onChange={changeStrengthValue}/>
          </Form.Item>
        </Form.Item>
        <Form.Item
          label="次数"
          style={{paddingTop: 0,marginBottom: "0px"}}
          className='count-item'
          >
          <Form.Item
            name={[...type, 'countCondition', 'conditionType']}
            style={{marginRight: '10px', width: '110px'}}
            rules={[({ getFieldValue }) => ({
              validator(_, value) {
                if (!value&&getFieldValue([...type, 'countCondition', 'value'])) {
                  return Promise.reject(new Error('请选择!'));
                }
                return Promise.resolve()
              }
            })]}
          >
            <Select 
            placeholder="请选择" 
            allowClear={true} 
            onChange={changeCount}
            >
            {
              strengths.map((it: any) => (
                <Option key={it.value} value={it.value} disabled={(['最大', '最小'].includes(strengthType)) && (['最大', '最小'].includes(it.value))}>{it.label}</Option>
              ))
            }
            </Select>
          </Form.Item>
          <Form.Item 
          name={[...type, 'countCondition', 'value']} 
          style={{width: '110px'}}
          rules={[({ getFieldValue }) => ({
            validator(_, value) {
              if (!value&&getFieldValue([...type, 'countCondition', 'conditionType'])) {
                return Promise.reject(new Error('请输入!'));
              }
              return Promise.resolve()
            }
          })]}
          >
            <InputNumber min={1} placeholder="请输入" onChange={changeCountValue}/>
          </Form.Item>
        </Form.Item>
    </>
  )
}

// 边类型表单多选
export const EdgeTypeItem = (props: any) => {
  const [prevSelectedValue, setPrevSelectedValue] = useState<string[]>([]) // 之前被选中的边类型合集
  let {edgeTypeNameList} = props

  // select下拉多选
  function handleChange(value:any,option:any) {
    if (value.includes('all')) {
      value = prevSelectedValue.includes('all')
              ? value.filter((item:any) => item!=='all')
              : ['all'].concat(edgeTypeNameList.map((item:any) => {return item.name}))
    } else {
      if (prevSelectedValue.includes('all')) {
        value = []
      } else if (value.length === edgeTypeNameList.length) {
        value = ['all', ...value]
      }
    }
    props.handleEdgeChange(value)
    setPrevSelectedValue(value)
  }
  return (
    <Form.Item label="边类型" name="edgeTypeNameList" rules={[{required: true, message: '请选择边类型'}]}>
      <Select
        mode="multiple"
        showSearch
        autoClearSearchValue={false}
        allowClear={true} 
        style={{width: '100%'}}
        placeholder="请选择边类型"
        onChange={handleChange}
        maxTagCount="responsive"
      >
        <Option key="all" value="all">全部</Option>
        {
          edgeTypeNameList.map((it: any) => (
            <Option key={it.id} value={it.name}>{it.name}</Option>
          ))
        }
      </Select>
    </Form.Item>
  )
}
// 边类型表单单选
export const EdgeTypeSingleItem = (props: any) => {
  return (
    <Form.Item 
      label="边类型"
      name="edgeTypeName"
      style={{minWidth: '240px'}}
      rules={[{required: true, message: '请选择类型'}]}
    >
      <Select showSearch allowClear={true} placeholder="请选择">
      {
        props.edgeTypeNameList?.map((item:any) => <Select.Option key={item.id} value={item.name}>{item.name}</Select.Option>)
      }
      </Select>
    </Form.Item>
  )
}