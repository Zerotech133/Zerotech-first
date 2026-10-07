package com.qianjiangsoft.graphapi.service.impl;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.date.DateUtil;
import cn.hutool.core.util.IdUtil;
import cn.hutool.core.util.StrUtil;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.github.pagehelper.PageHelper;
import com.qianjiangsoft.graphapi.service.BaseLibraryGroupService;
import com.qianjiangsoft.graphapi.service.BaseLibraryService;
import com.qianjiangsoft.graphcore.constants.BaseConstants;
import com.qianjiangsoft.graphcore.constants.ResultCode;
import com.qianjiangsoft.graphcore.dto.BaseLibraryDTO;
import com.qianjiangsoft.graphcore.dto.BaseLibraryGroupDTO;
import com.qianjiangsoft.graphcore.dto.BaseProcessLibraryTreeNodeDTO;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.domain.entity.BaseLibrary;
import com.qianjiangsoft.graphcore.domain.entity.BaseLibraryGroup;
import com.qianjiangsoft.graphcore.exception.BusinessException;
import com.qianjiangsoft.graphcore.mapper.BaseLibraryGroupMapper;
import com.qianjiangsoft.graphcore.mapper.BaseLibraryMapper;
import com.qianjiangsoft.graphcore.param.BaseLibraryParam;
import com.qianjiangsoft.graphcore.domain.param.DaoSelectBaseLibraryListWithCountParam;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.List;

/**
 * 基础-分组信息Service业务层处理
 *
 * @author qianjiang
 * @date 2021-03-25
 */
@Service
@RequiredArgsConstructor
public class BaseLibraryServiceImpl implements BaseLibraryService {

	@Autowired
	BaseLibraryMapper baseLibraryMapper;

	@Autowired
	BaseLibraryGroupMapper baseLibraryGroupMapper;

	@Autowired
	BaseLibraryGroupService baseLibraryGroupService;

	/**
	 * 查询基础-分组信息列表带有表个数
	 *
	 * @return 基础-分组信息
	 *
	 **/
	@Override
	public List<BaseProcessLibraryTreeNodeDTO> getLibraryAndGroupTreeNodeList(){

		//过程库分组列表
		List<BaseProcessLibraryTreeNodeDTO> treeNodeDTOList = new ArrayList<>();

		//分组列表
		BaseLibraryGroup baseLibraryGroupParam = new BaseLibraryGroup();
		if ( SpaceContextHolder.get()==null ||StrUtil.isEmpty(SpaceContextHolder.getSpaceId())) {
			return treeNodeDTOList;
		}
		baseLibraryGroupParam.setSpaceId(SpaceContextHolder.getSpaceId());

		List<BaseLibraryGroupDTO> baseLibraryGroupList = baseLibraryGroupService.selectBaseLibraryGroupList();

		baseLibraryGroupList.forEach(baseLibraryGroup -> {
			BaseProcessLibraryTreeNodeDTO treeNodeDTO = BeanUtil.copyProperties(baseLibraryGroup, BaseProcessLibraryTreeNodeDTO.class);
			treeNodeDTO.setType(BaseConstants.TYPE_NORMAL);
			treeNodeDTOList.add(treeNodeDTO);
		});

		//过程库列表
		DaoSelectBaseLibraryListWithCountParam param = new DaoSelectBaseLibraryListWithCountParam();
		param.setSpaceId(SpaceContextHolder.getSpaceId());
		List<BaseLibrary> processLibraryList = baseLibraryMapper.selectWithGraphCountByParam(param);
		//查询库表数量
		processLibraryList.forEach((baseLibrary -> {
			BaseProcessLibraryTreeNodeDTO treeNodeDTO = BeanUtil.copyProperties(baseLibrary, BaseProcessLibraryTreeNodeDTO.class);
			treeNodeDTO.setTableCount(baseLibrary.getRelationalGraphCount());
			treeNodeDTO.setParentId(baseLibrary.getGroupId());
			treeNodeDTO.setType(BaseConstants.TYPE_RELATIONAL_GRAPH);
			treeNodeDTOList.add(treeNodeDTO);
		}));

		return treeNodeDTOList;
	}

	@Override
	public List<BaseLibraryDTO> queryLibraryListByGroupId(String groupId) {
		LambdaQueryWrapper<BaseLibrary> queryWrapper = new LambdaQueryWrapper();
		queryWrapper.eq(BaseLibrary::getGroupId, groupId)
				.eq(BaseLibrary::getSpaceId, SpaceContextHolder.getSpaceId());
		PageHelper.orderBy("create_time desc");

		return BeanUtil.copyToList(baseLibraryMapper.selectList(queryWrapper), BaseLibraryDTO.class);
	}

	/**
	 * 查询基础-分组信息
	 *
	 * @param id 主键ID
	 * @return 基础-分组信息
	 */
	@Override
	public BaseLibraryDTO selectBaseLibraryById(String id) {
		return BeanUtil.copyProperties(baseLibraryMapper.selectById(id), BaseLibraryDTO.class);
	}

	/**
	 * 新增基础-库
	 *
	 * @param param 基础-库
	 * @return 结果
	 */
	@Override
	public void insertBaseLibrary(BaseLibraryParam param) {

		BaseLibrary baseLibrary = BeanUtil.copyProperties(param, BaseLibrary.class);

		// 先判断是否存在本次分组名
		this.checkLibraryNameExists(param);

		//设置属性
		baseLibrary.setId(IdUtil.simpleUUID());
		baseLibrary.setCreateTime(DateUtil.date());
		baseLibrary.setSpaceId(SpaceContextHolder.getSpaceId());
		//如果没有分组设置固定值
		if(StrUtil.isEmpty(param.getGroupId())) {
			baseLibrary.setGroupId(BaseConstants.NULL_PARENT_ID);
		}

		baseLibraryMapper.insert(baseLibrary);

		//回传id
		param.setId(baseLibrary.getId());
	}

	/**
	 * 修改基础-分组信息
	 *
	 * @param baseLibraryParam 基础-分组信息
	 * @return 结果
	 */
	@Override
	public void updateBaseLibrary(BaseLibraryParam baseLibraryParam) {

		// 先判断是否存在本次分组名
		checkLibraryNameExists(baseLibraryParam);

		//更新分组信息
		BaseLibrary baseLibrary = BeanUtil.copyProperties(baseLibraryParam, BaseLibrary.class);

		//如果没有分组设置固定值
		if(StrUtil.isEmpty(baseLibraryParam.getGroupId())) {
			baseLibrary.setGroupId(BaseConstants.NULL_PARENT_ID);
		}
		baseLibraryMapper.updateById(baseLibrary);
	}

	@Override
	public void dragBaseLibrary(String id, String groupId) {
		BaseLibraryParam baseLibraryParam = new BaseLibraryParam();
		baseLibraryParam.setId(id);
		baseLibraryParam.setGroupId(groupId);

		//old library
		BaseLibrary baseLibraryOld = baseLibraryMapper.selectById(id);
		baseLibraryParam.setName(baseLibraryOld.getName());

		checkLibraryNameExists(baseLibraryParam);

		//更新
		BaseLibrary baseLibrary = BeanUtil.copyProperties(baseLibraryParam, BaseLibrary.class);
		baseLibraryMapper.updateById(baseLibrary);
	}


	/**
	 * 删除基础-分组信息信息
	 *
	 * @param id 基础-分组信息ID
	 * @return 结果
	 */
	@Override
	@Transactional(rollbackFor = Exception.class)
	public void deleteBaseLibraryById(String id) {

		// 新增案件日志
		BaseLibrary baseLibrary = baseLibraryMapper.selectById(id);

		if(baseLibrary == null){
			throw new BusinessException(ResultCode.BUSINESS_ERROR, "数据不存在");
		}

		baseLibraryMapper.deleteById(id);
	}

	/**
	 * 同一分组名称唯一性校验
	 *
	 * @param baseLibraryParam 分组对象
	 */
	@Override
	public void checkLibraryNameExists(BaseLibraryParam baseLibraryParam) {

		LambdaQueryWrapper<BaseLibrary> queryWrapper = new LambdaQueryWrapper<>();
		queryWrapper.eq(BaseLibrary::getName, baseLibraryParam.getName())
				.eq(BaseLibrary::getSpaceId, SpaceContextHolder.getSpaceId())
				.eq(BaseLibrary::getGroupId, baseLibraryParam.getGroupId())
				.ne(StrUtil.isNotEmpty(baseLibraryParam.getId()), BaseLibrary::getId, baseLibraryParam.getId());

		long count = baseLibraryMapper.selectCount(queryWrapper);

		if(count > 0) {
			throw new BusinessException(StrUtil.format("同一级别分组名称({})已存在", baseLibraryParam.getName()));
		}
	}
}
