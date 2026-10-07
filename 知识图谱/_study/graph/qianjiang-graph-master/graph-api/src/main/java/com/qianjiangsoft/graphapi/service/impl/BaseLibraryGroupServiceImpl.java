package com.qianjiangsoft.graphapi.service.impl;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.util.IdUtil;
import cn.hutool.core.util.StrUtil;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.qianjiangsoft.graphapi.service.BaseLibraryGroupService;
import com.qianjiangsoft.graphcore.constants.ResultCode;
import com.qianjiangsoft.graphcore.dto.BaseLibraryGroupDTO;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.domain.entity.BaseLibraryGroup;
import com.qianjiangsoft.graphcore.exception.BusinessException;
import com.qianjiangsoft.graphcore.mapper.BaseLibraryGroupMapper;
import com.qianjiangsoft.graphcore.mapper.BaseLibraryMapper;
import com.qianjiangsoft.graphcore.model.Space;
import com.qianjiangsoft.graphcore.param.BaseLibraryGroupParam;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.List;

@Service
@RequiredArgsConstructor
public class BaseLibraryGroupServiceImpl implements BaseLibraryGroupService {

    @Autowired
    BaseLibraryMapper baseLibraryMapper;

    @Autowired
    BaseLibraryGroupMapper baseLibraryGroupMapper;


    /**
     * 查询基础-分组信息列表
     *
     * @return 基础-分组信息
     */
    @Override
    public List<BaseLibraryGroupDTO> selectBaseLibraryGroupList() {
        if (StrUtil.isEmpty(SpaceContextHolder.getSpaceId()) || SpaceContextHolder.get() == null) {
            return new ArrayList<>();
        }
        Space space = SpaceContextHolder.get();

        LambdaQueryWrapper<BaseLibraryGroup> queryWrapper = new LambdaQueryWrapper<>();
        queryWrapper.eq(BaseLibraryGroup::getSpaceId, space.getId());
        List<BaseLibraryGroup> baseLibraryGroupList = baseLibraryGroupMapper.selectList(queryWrapper);

        return BeanUtil.copyToList(baseLibraryGroupList, BaseLibraryGroupDTO.class);
    }

    /**
     * 查询基础-分组信息
     *
     * @param id 主键ID
     * @return 基础-分组信息
     */
    @Override
    public BaseLibraryGroupDTO selectBaseLibraryGroupById(String id) {
        return BeanUtil.copyProperties(baseLibraryMapper.selectById(id), BaseLibraryGroupDTO.class);
    }

    /**
     * 新增基础-分组信息
     *
     * @param baseLibraryGroupParam 基础-分组信息
     * @return 结果
     */
    @Override
    public void insertBaseTableGroup(BaseLibraryGroupParam baseLibraryGroupParam) {

        Space space = SpaceContextHolder.get();

        BaseLibraryGroup baseLibraryGroup = BeanUtil.copyProperties(baseLibraryGroupParam, BaseLibraryGroup.class);

        // 先判断是否存在本次分组名
        this.checkGroupNameExists(baseLibraryGroupParam);

        //设置属性
        baseLibraryGroup.setId(IdUtil.simpleUUID());
        baseLibraryGroup.setSpaceId(space.getId());

        baseLibraryGroupMapper.insert(baseLibraryGroup);
    }

    /**
     * 修改基础-分组信息
     *
     * @param baseLibraryGroupParam 基础-分组信息
     * @return 结果
     */
    @Override
    public void updateBaseTableGroup(BaseLibraryGroupParam baseLibraryGroupParam) {

        BaseLibraryGroup baseLibraryGroup = BeanUtil.copyProperties(baseLibraryGroupParam, BaseLibraryGroup.class);

        // 先判断是否存在本次分组名
        checkGroupNameExists(baseLibraryGroupParam);

        baseLibraryGroupMapper.updateById(baseLibraryGroup);
    }


    /**
     * 删除基础-分组信息信息
     *
     * @param id 基础-分组信息ID
     * @return 结果
     */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public void deleteBaseLibraryGroupById(String id) {

        // 新增案件日志
        BaseLibraryGroup tableGroup = baseLibraryGroupMapper.selectById(id);
        if(tableGroup == null){
            throw new BusinessException(ResultCode.BUSINESS_ERROR, "数据不存在");
        }

        baseLibraryGroupMapper.deleteById(id);
    }

    /**
     * 同一分组名称唯一性校验
     *
     * @param baseLibraryGroupParam 分组对象
     */
    @Override
    public void checkGroupNameExists(BaseLibraryGroupParam baseLibraryGroupParam) {

        Space space = SpaceContextHolder.get();

        BaseLibraryGroup baseLibraryGroup = BeanUtil.copyProperties(baseLibraryGroupParam, BaseLibraryGroup.class);
        baseLibraryGroup.setSpaceId(space.getId());

        //查询数据库
        LambdaQueryWrapper<BaseLibraryGroup> queryWrapper = Wrappers.lambdaQuery();
        queryWrapper.ne(StrUtil.isNotEmpty(baseLibraryGroup.getId()), BaseLibraryGroup::getId, baseLibraryGroup.getId())
                .eq(BaseLibraryGroup::getName, baseLibraryGroup.getName())
                .eq(BaseLibraryGroup::getSpaceId, baseLibraryGroup.getSpaceId());
        long count = baseLibraryGroupMapper.selectCount(queryWrapper);

        if(count > 0) {
            throw new BusinessException(ResultCode.BUSINESS_ERROR, "分组已经存在");
        }
    }

    @Override
    public void dragBaseLibraryGroup(String id, String parentId) {
        BaseLibraryGroupParam libraryGroupParam = new BaseLibraryGroupParam();
        libraryGroupParam.setId(id);
        libraryGroupParam.setParentId(parentId);

        //更新
        BaseLibraryGroup libraryGroup = BeanUtil.copyProperties(libraryGroupParam, BaseLibraryGroup.class);
        baseLibraryGroupMapper.updateById(libraryGroup);
    }
}
